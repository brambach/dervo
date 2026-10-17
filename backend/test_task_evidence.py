"""Boundary tests use temporary projects and a temporary Store only."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from dervo_connections import Store, Rejected


class TaskEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'target'
        self.root.mkdir()
        (self.root / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertEqual(1, 1)\n')
        self.dbpath = self.base / 'store.sqlite'
        self.store = Store(self.dbpath)
        self.addCleanup(lambda: self.store.close())
        self.store.add_profile('profile', 'codex', self.base / 'identity', 'local-test', 'personal')
        self.store.add_project('project', self.root, 'profile', 'test-model')
        self.store.add_thread('thread', 'project', 'channel', 'private', 'alice')
        self.ctx = dict(thread_id='thread', project_id='project', actor='alice')

    def service(self):
        import task_evidence
        return task_evidence.TaskEvidence(self.store)

    def create(self, service):
        return service.create(files=['test_sample.py'], test_file='test_sample.py', **self.ctx)

    def prepare_diff(self, left, right):
        service = self.service()
        (self.root / 'sample.txt').write_bytes(left)
        task = service.create(files=['sample.txt', 'test_sample.py'],
                              test_file='test_sample.py', **self.ctx)
        service.approve(task['id'], scope_version=1, **self.ctx)
        pre = service.read_file(task['id'], 'sample.txt', **self.ctx)
        return service.prepare(task['id'], changes={
            'sample.txt': {'expected_sha256': pre['sha256'], 'content': right}}, **self.ctx)

    def test_diff_missing_eof_newlines(self):
        attempt = self.prepare_diff(b'old', b'new')
        self.assertEqual(attempt['diff']['sample.txt'],
                         '--- before/sample.txt\n+++ after/sample.txt\n@@ -1 +1 @@\n'
                         '-old\n\\ No newline at end of file\n'
                         '+new\n\\ No newline at end of file\n')
        self.assertNotIn('-old+new', attempt['diff']['sample.txt'])

    def test_diff_non_utf8_bytes_are_visible(self):
        for left, right in ((b'a\xff\n', b'a\xfe\n'), (b'a\n', b'a\xff\n')):
            with self.subTest(left=left, right=right):
                diff = self.prepare_diff(left, right)['diff']['sample.txt']
                self.assertEqual(diff,
                                 'Binary diff (hex bytes):\n'
                                 '--- before/sample.txt\n+++ after/sample.txt\n@@ -1 +1 @@\n'
                                 + '-' + left.hex(' ') + '\n'
                                 + '+' + right.hex(' ') + '\n')
                self.assertNotIn('\ufffd', diff)

    def test_diff_newline_only_change(self):
        for left, right, body in (
                (b'old', b'old\n', '-old\n\\ No newline at end of file\n+old\n'),
                (b'old\n', b'old', '-old\n+old\n\\ No newline at end of file\n'),
                ('caf\u00e9\n'.encode(), 'th\u00e9\n'.encode(), '-caf\u00e9\n+th\u00e9\n')):
            with self.subTest(left=left, right=right):
                diff = self.prepare_diff(left, right)['diff']['sample.txt']
                self.assertEqual(diff, '--- before/sample.txt\n+++ after/sample.txt\n'
                                 '@@ -1 +1 @@\n' + body)

    def test_diff_unchanged_file_is_empty(self):
        for content in (b'old', b'old\n', b'a\xff\n', b''):
            with self.subTest(content=content):
                attempt = self.prepare_diff(content, content)
                self.assertEqual(attempt['diff'], {'sample.txt': '', 'test_sample.py': ''})
                self.assertEqual(attempt['before'], attempt['after'])
                self.assertEqual(attempt['state'], 'prepared')
                self.assertIsNone(attempt['test'])

    def test_diff_multifile_successive_attempts_use_current_preimages(self):
        import base64
        import hashlib

        def snapshots(contents):
            return {path: {'sha256': hashlib.sha256(data).hexdigest(),
                           'bytes_b64': base64.b64encode(data).decode('ascii')}
                    for path, data in contents.items()}

        original = {'sample.txt': b'old', 'other.txt': b'a\xff\n',
                    'test_sample.py': (self.root / 'test_sample.py').read_bytes()}
        for path, data in original.items():
            (self.root / path).write_bytes(data)
        service = self.service()
        task = service.create(files=list(original), test_file='test_sample.py', **self.ctx)
        service.approve(task['id'], scope_version=1, **self.ctx)
        first_bytes = {**original, 'sample.txt': b'new', 'other.txt': b'a\xfe\n'}
        first = service.prepare(task['id'], changes={
            path: {'expected_sha256': snapshots(original)[path]['sha256'], 'content': first_bytes[path]}
            for path in ('sample.txt', 'other.txt')}, **self.ctx)
        second_bytes = {**first_bytes, 'sample.txt': b'final\n', 'other.txt': b'a\xfd\n'}
        second = service.prepare(task['id'], changes={
            path: {'expected_sha256': snapshots(first_bytes)[path]['sha256'], 'content': second_bytes[path]}
            for path in ('sample.txt', 'other.txt')}, **self.ctx)
        self.assertEqual(first['before'], snapshots(original))
        self.assertEqual(first['after'], snapshots(first_bytes))
        self.assertEqual(second['before'], snapshots(first_bytes))
        self.assertEqual(second['after'], snapshots(second_bytes))
        self.assertEqual(second['before'], first['after'])
        self.assertEqual(second['diff'], {
            'sample.txt': '--- before/sample.txt\n+++ after/sample.txt\n@@ -1 +1 @@\n'
                          '-new\n\\ No newline at end of file\n+final\n',
            'other.txt': 'Binary diff (hex bytes):\n--- before/other.txt\n+++ after/other.txt\n'
                         '@@ -1 +1 @@\n-61 fe 0a\n+61 fd 0a\n',
            'test_sample.py': ''})
        self.assertNotEqual(first['diff'], second['diff'])
        for number, attempt in enumerate((first, second), 1):
            self.assertEqual(attempt['number'], number)
            self.assertEqual(attempt['scope_version'], 1)
            self.assertEqual(attempt['files'], sorted(original))
            self.assertEqual(attempt['argv'], task['argv'])
            self.assertEqual(attempt['state'], 'prepared')
            self.assertIsNone(attempt['test'])
        self.assertEqual({path: (self.root / path).read_bytes() for path in original}, second_bytes)
        with self.assertRaises(Rejected):
            service.launch(task['id'], attempt_number=1, **self.ctx)
        self.assertIsNone(service.read(task['id'], **self.ctx)['review'])
        self.store.close()
        self.store = Store(self.dbpath)
        self.assertEqual(self.service().evidence(task['id'], **self.ctx), [first, second])

    @unittest.skipUnless(sys.platform == 'win32', 'the Windows app container')
    def test_actual_windows_container_canaries(self):
        # Windows has no fork and a blocked connect times out, so the test listens and checks nothing reached it. The
        # same file run unconfined has to fail, or the canary would prove nothing.
        import socket
        import subprocess
        import task_evidence
        read_canary = self.base / 'outside-secret'
        read_canary.write_text('do not read')
        write_canary = self.base / 'outside-write'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.setblocking(False)
            body = f'''import os, socket, subprocess, sys, unittest, winreg
class Canary(unittest.TestCase):
 def test_boundary(self):
  self.assertEqual(os.environ.get('HOME'), '')
  self.assertNotIn('PATH', os.environ)
  for path, mode in [({str(read_canary)!r}, 'r'), ({str(write_canary)!r}, 'w'), ('local-write', 'w'), (os.path.join(os.environ['TEMP'], 'x'), 'w')]:
   with self.assertRaises(PermissionError, msg=path):
    with open(path, mode): pass
  with self.assertRaises(PermissionError): os.listdir({str(Path.home())!r})
  for key in ('Environment', 'Software'):
   with self.assertRaises(PermissionError, msg=key): winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
  with socket.socket() as sock:
   sock.settimeout(2)
   with self.assertRaises(OSError): sock.connect(('127.0.0.1', {listener.getsockname()[1]}))
  with self.assertRaises(OSError): subprocess.run([sys.executable, '-c', '0'])
  print('canaries-denied')
'''
            (self.root / 'test_sample.py').write_text(body)
            task = self.create(self.service())
            result = task_evidence._run_confined(self.root, task['argv'], timeout=20)
            with self.assertRaises(BlockingIOError):
                listener.accept()
        self.assertEqual(result['confinement'], 'windows-appcontainer')
        self.assertEqual(result['exit_status'], 0, {k: v for k, v in result.items() if k != 'runtime_files'})
        self.assertIn('canaries-denied', result['stdout'])
        self.assertIn('Ran 1 test', result['stderr'])
        self.assertFalse(write_canary.exists())
        self.assertFalse((self.root / 'local-write').exists())
        control = subprocess.run(task['argv'], cwd=self.root, env={'HOME': '', 'SystemRoot': os.environ['SystemRoot']},
                                 capture_output=True, text=True, timeout=60)
        self.assertNotEqual(control.returncode, 0, 'the canary passes unconfined, so it proves nothing')
        print('WINDOWS CANARIES:', {k: v for k, v in result.items() if k != 'runtime_files'})

    @unittest.skipIf(sys.platform == 'win32', 'fork and a refused connect are the Mac canary; Windows has its own above')
    def test_actual_sandbox_canaries(self):
        import task_evidence
        self.assertTrue(hasattr(task_evidence, '_run_confined'), 'confined verifier is missing')
        read_canary = self.base / 'outside-secret'
        read_canary.write_text('do not read')
        write_canary = self.base / 'outside-write'
        body = f'''import errno, os, socket, unittest
class Canary(unittest.TestCase):
 def test_boundary(self):
  self.assertEqual(os.environ.get('HOME'), '')
  self.assertNotIn('PATH', os.environ)
  for path, mode in [({str(read_canary)!r}, 'r'), ({str(write_canary)!r}, 'w'), ('local-write', 'w')]:
   with self.assertRaises(PermissionError):
    with open(path, mode): pass
  with socket.socket() as sock:
   with self.assertRaises(PermissionError): sock.connect(('127.0.0.1', 9))
  with self.assertRaises(PermissionError): os.fork()
  print('canaries-denied')
'''
        if sys.platform.startswith('linux'):
            # Under bubblewrap a path outside the snapshot doesn't exist inside, the snapshot is read-only (EROFS), and the
            # seccomp filter refuses an internet socket itself, before any connect (linux_confinement.py).
            body = f'''import errno, os, socket, unittest
class Canary(unittest.TestCase):
 def test_boundary(self):
  self.assertEqual(os.environ.get('HOME'), '')
  self.assertNotIn('PATH', os.environ)
  with self.assertRaises(FileNotFoundError): open({str(read_canary)!r})
  with self.assertRaises(OSError): open({str(write_canary)!r}, 'w')  # EROFS: its folder is above the snapshot's mount
  with self.assertRaises(OSError) as caught: open('local-write', 'w')
  self.assertEqual(caught.exception.errno, errno.EROFS)
  with self.assertRaises(PermissionError): socket.socket()
  with self.assertRaises(PermissionError): os.fork()
  print('canaries-denied')
'''
        (self.root / 'test_sample.py').write_text(body)
        task = self.create(self.service())
        try:
            result = task_evidence._run_confined(self.root, task['argv'])
        except task_evidence.ConfinementBlocked as exc:
            print('SANDBOX BLOCKER:', str(exc))
            self.skipTest(str(exc))
        self.assertEqual(result['exit_status'], 0, {k: v for k, v in result.items() if k != 'runtime_files'})
        self.assertIn('canaries-denied', result['stdout'])
        self.assertIn('Ran 1 test', result['stderr'])
        self.assertFalse(write_canary.exists())
        print('SANDBOX CANARIES:', {k: v for k, v in result.items() if k != 'runtime_files'})

    def test_change_evidence_correction_and_review(self):
        import base64
        service = self.service()
        self.assertTrue(hasattr(service, 'attempt'), 'task attempts are missing')
        task_id = self.create(service)['id']
        pre = service.read_file(task_id, 'test_sample.py', **self.ctx)
        changed = base64.b64decode(pre['bytes_b64']).replace(b'(1, 1)', b'(2, 2)')
        changes = {'test_sample.py': {'expected_sha256': pre['sha256'], 'content': changed}}
        with self.assertRaises(Rejected):
            service.attempt(task_id, changes=changes, **self.ctx)
        service.approve(task_id, scope_version=1, **self.ctx)
        attempt = service.attempt(task_id, changes=changes, **self.ctx)
        self.assertEqual(attempt['number'], 1)
        self.assertEqual(attempt['before']['test_sample.py'], pre)
        self.assertEqual(base64.b64decode(attempt['after']['test_sample.py']['bytes_b64']), changed)
        self.assertIn('- def test_ok', attempt['diff']['test_sample.py'])
        self.assertIn('+ def test_ok', attempt['diff']['test_sample.py'])
        self.assertEqual((self.root / 'test_sample.py').read_bytes(), changed)
        self.assertIn(attempt['state'], ('verified', 'blocked'))
        if attempt['state'] == 'verified':
            self.assertEqual(attempt['test']['exit_status'], 0)
            self.assertIn('Ran 1 test', attempt['test']['stderr'])
        else:
            self.assertEqual(attempt['blocker'], __import__('task_evidence').CONFINEMENT_PLAIN)  # plain words, polish review 9 Oct
            self.assertIsNone(attempt['test'])
            with self.assertRaises(Rejected):
                service.review(task_id, attempt_number=1, decision='approved', **self.ctx)
        service.review(task_id, attempt_number=1, decision='rejected', **self.ctx)
        self.assertEqual(service.read(task_id, **self.ctx)['review']['decision'], 'rejected')
        with self.assertRaises(Rejected):
            service.attempt(task_id, changes=changes, **self.ctx)
        newer = service.read_file(task_id, 'test_sample.py', **self.ctx)
        second = service.attempt(task_id, changes={'test_sample.py': {'expected_sha256': newer['sha256'], 'content': changed + b'\n'}}, **self.ctx)
        self.assertEqual(second['number'], 2)
        self.assertIsNone(service.read(task_id, **self.ctx)['review'])
        service.review(task_id, attempt_number=2, decision='rejected', **self.ctx)
        (self.root / 'test_sample.py').write_bytes(changed + b'\n# external edit\n')
        self.assertIsNone(service.read(task_id, **self.ctx)['review'])
        with self.assertRaises(Rejected):
            service.review(task_id, attempt_number=2, decision='rejected', **self.ctx)
        for method in ('evidence', 'review'):
            with self.assertRaises(Rejected):
                getattr(service, method)(task_id, **{**self.ctx, 'actor': 'bob'}, **({'attempt_number': 2, 'decision': 'rejected'} if method == 'review' else {}))
        self.store.close()
        self.store = Store(self.dbpath)
        saved = self.service().evidence(task_id, **self.ctx)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]['before']['test_sample.py'], pre)
        print('ATTEMPT EVIDENCE:', {**{k: v for k, v in attempt.items() if k not in ('before', 'after', 'test')},
                                    'test': {k: v for k, v in (attempt['test'] or {}).items() if k != 'runtime_files'}})

    def test_sensitive_variants_and_no_supplied_results(self):
        service = self.service()
        task_id = self.create(service)['id']
        # 16.2: names are refused by secret shape, not first word, so profile_backup.txt became db_password.txt.
        for path in ('auth-token.json', 'db_password.txt', '.config/key'):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('sensitive canary')
            with self.subTest(path=path), self.assertRaises(Rejected):
                service.scope(task_id, files=['test_sample.py', path], test_file='test_sample.py', **self.ctx)
        service.approve(task_id, scope_version=1, **self.ctx)
        with self.assertRaises(TypeError):
            service.attempt(task_id, changes={}, test_results={'exit_status': 0}, **self.ctx)

    def test_symlink_swap_and_hardlink_write_denied(self):
        import os
        service = self.service()
        task_id = self.create(service)['id']
        service.approve(task_id, scope_version=1, **self.ctx)
        pre = service.read_file(task_id, 'test_sample.py', **self.ctx)
        outside = self.base / 'outside.py'
        outside.write_text('outside canary')
        target = self.root / 'test_sample.py'
        target.unlink()
        for mode in ('symlink', 'hardlink'):
            if mode == 'symlink':
                target.symlink_to(outside)
            else:
                os.link(outside, target)
            with self.assertRaises(Rejected):
                service.attempt(task_id, changes={'test_sample.py': {'expected_sha256': pre['sha256'], 'content': b'overwrite'}}, **self.ctx)
            self.assertEqual(outside.read_text(), 'outside canary')
            target.unlink()

    def test_actual_timeout_output_bound_and_cleanup(self):
        import os
        import task_evidence
        task = self.create(self.service())
        target = self.root / 'test_sample.py'
        target.write_text('import os,time\nprint(os.getpid(), flush=True)\ntime.sleep(30)\n')
        windows = sys.platform == 'win32'
        killed = 1 if windows else -9  # Windows ends the check's job with exit code 1
        result = task_evidence._run_confined(self.root, task['argv'], timeout=2 if windows else 0.6)  # a container takes longer to start
        self.assertEqual(result['stopped'], 'timeout', result['stderr'])
        self.assertEqual(result['exit_status'], killed)
        pid = int(result['stdout'].strip())
        if windows:  # os.kill would end the process, not ask about it
            import ctypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if handle:
                try:
                    self.assertEqual(kernel32.WaitForSingleObject(handle, 0), 0, 'the timed-out check is still running')
                finally:
                    kernel32.CloseHandle(handle)
        elif sys.platform.startswith('linux'):
            # The pid printed is the check's own in its PID namespace, not one the host can signal. What's left of the
            # check would still be working in the snapshot, so no process may have it as its folder.
            root = str(self.root.resolve())
            for proc in Path('/proc').iterdir():
                try:
                    self.assertNotEqual(os.readlink(proc / 'cwd'), root, 'the timed-out check is still running')
                except OSError:
                    pass
        else:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        print('TIMEOUT CLEANUP:', {k: v for k, v in result.items() if k != 'runtime_files'})
        target.write_text('import os\nwhile True: os.write(1, b"x" * 4096)\n')
        result = task_evidence._run_confined(self.root, task['argv'], output_limit=4096)
        self.assertEqual(result['stopped'], 'output_limit')
        self.assertLessEqual(len(result['stdout'].encode()) + len(result['stderr'].encode()), 4096)
        self.assertEqual(result['exit_status'], killed)
        print('OUTPUT BOUND:', len(result['stdout']), len(result['stderr']), result['exit_status'])
        for argv in ([task['argv'][0], '-c', 'print(1)'], task['argv'][:-1] + ['test*.py']):
            with self.assertRaises(Rejected):
                task_evidence._run_confined(self.root, argv)

    def test_actual_snapshot_hides_original_unlisted_files(self):
        service = self.service()
        secret = self.root / 'unlisted-secret'
        secret.write_text('private canary')
        source = f'''import unittest
class Snapshot(unittest.TestCase):
 def test_hidden(self):
  with self.assertRaises(FileNotFoundError): open('unlisted-secret')
  with self.assertRaises({'FileNotFoundError' if sys.platform.startswith('linux') else 'PermissionError'}): open({str(secret)!r})
'''
        target = self.root / 'test_sample.py'
        target.write_text(source)
        task_id = self.create(service)['id']
        pre = service.read_file(task_id, 'test_sample.py', **self.ctx)
        service.approve(task_id, scope_version=1, **self.ctx)
        result = service.attempt(task_id, changes={'test_sample.py': {'expected_sha256': pre['sha256'], 'content': source.encode() + b'\n'}}, **self.ctx)
        self.assertEqual(result['state'], 'verified', result['blocker'])
        self.assertEqual(result['test']['exit_status'], 0, result['test']['stderr'])
        service.review(task_id, attempt_number=1, decision='approved', **self.ctx)
        self.assertEqual(service.read(task_id, **self.ctx)['review']['decision'], 'approved')
        service.scope(task_id, files=['test_sample.py'], test_file='test_sample.py', **self.ctx)
        self.assertIsNone(service.read(task_id, **self.ctx)['review'])
        with self.assertRaises(Rejected):
            service.review(task_id, attempt_number=1, decision='approved', **self.ctx)

    def test_nonblocking_job_poll_cancel_without_worker_sqlite(self):
        import time
        service = self.service()
        self.assertTrue(hasattr(service, 'launch'), 'nonblocking verification jobs are missing')
        task_id = self.create(service)['id']
        service.approve(task_id, scope_version=1, **self.ctx)
        pre = service.read_file(task_id, 'test_sample.py', **self.ctx)
        attempt = service.prepare(task_id, changes={'test_sample.py': {'expected_sha256': pre['sha256'], 'content': b'import time\ntime.sleep(30)\n'}}, **self.ctx)
        self.assertEqual(attempt['state'], 'prepared')
        started = time.monotonic()
        job = service.launch(task_id, attempt_number=1, **self.ctx)
        self.assertLess(time.monotonic() - started, 0.5)
        with self.assertRaises(Rejected):
            service.poll(task_id, job_id=job, **{**self.ctx, 'actor': 'bob'})
        service.cancel(task_id, job_id=job, **self.ctx)
        while time.monotonic() - started < 3:
            status = service.poll(task_id, job_id=job, **self.ctx)
            if status['state'] != 'running':
                break
            time.sleep(0.02)
        self.assertEqual(status['state'], 'cancelled', status)
        self.assertIsNone(service.read(task_id, **self.ctx)['review'])
        print('NONBLOCKING CANCEL:', status['state'])

    def test_restart_marks_unconfirmed_job_uncertain(self):
        import json
        service = self.service()
        task = self.create(service)
        task['attempts'] = [{'number': 1, 'state': 'running'}]
        with self.store.db:
            self.store.db.execute('UPDATE task_evidence SET document=? WHERE id=?', (json.dumps(task), task['id']))
        self.store.close()
        self.store = Store(self.dbpath)
        service = self.service()
        self.assertEqual(service.read(task['id'], **self.ctx)['attempts'][0]['state'], 'uncertain')

    def test_runtime_abort_fails_closed(self):
        import task_evidence
        task = self.create(self.service())
        try:
            result = task_evidence._run_confined(self.root, task['argv'])
        except task_evidence.ConfinementBlocked as exc:
            self.assertIn('Confinement', str(exc))
            print('FAIL CLOSED:', str(exc))
        else:
            self.assertGreaterEqual(result['exit_status'], 0, 'runtime abort must be an explicit confinement blocker')

    def test_allowlist_broker_and_scope_approval(self):
        service = self.service()
        self.assertTrue(hasattr(service, 'read_file'), 'host file broker is missing')
        task = self.create(service)
        task_id = task['id']
        before = service.read_file(task_id, 'test_sample.py', **self.ctx)
        self.assertIn('sha256', before)
        self.assertIn('bytes_b64', before)
        task = service.approve(task_id, scope_version=1, **self.ctx)
        self.assertEqual(task['approval']['scope_version'], 1)
        self.assertIsNone(task['review'])
        for path in ('/tmp/no', '../no', 'a/../no', '.env', '.env.local', 'auth/key', 'profiles/a', 'data/a', '.git/config', 'else.py'):
            with self.subTest(path=path), self.assertRaises(Rejected):
                service.read_file(task_id, path, **self.ctx)
        outside = self.base / 'secret'
        outside.write_text('private canary')
        (self.root / 'link.py').symlink_to(outside)
        (self.root / 'directory').symlink_to(self.base, target_is_directory=True)
        for path in ('link.py', 'directory/secret'):
            with self.assertRaises(Rejected):
                service.scope(task_id, files=['test_sample.py', path], test_file='test_sample.py', **self.ctx)
        for filename in ('.env', 'auth.py', 'profile.json', 'data/key', '.git/config'):
            with self.assertRaises(Rejected):
                service.scope(task_id, files=['test_sample.py', filename], test_file='test_sample.py', **self.ctx)
        with self.assertRaises(Rejected):
            service.scope(task_id, files=['test_sample.py'], test_file='test*.py', **self.ctx)
        task = service.scope(task_id, files=['test_sample.py'], test_file='test_sample.py', **self.ctx)
        self.assertEqual(task['scope_version'], 2)
        self.assertIsNone(task['approval'])
        with self.assertRaises(Rejected):
            service.approve(task_id, scope_version=1, **self.ctx)
        with self.assertRaises(Rejected):
            service.read_file(task_id, 'test_sample.py', **{**self.ctx, 'actor': 'bob'})

    def test_persisted_binding_and_private_access(self):
        self.assertIsNotNone(importlib.util.find_spec('task_evidence'), 'task/evidence module is missing')
        service = self.service()
        task = self.create(service)
        self.store.close()
        self.store = Store(self.dbpath)
        service = self.service()
        saved = service.read(task['id'], **self.ctx)
        self.assertEqual(saved['profile_id'], 'profile')
        self.assertEqual(saved['model'], 'test-model')
        self.assertEqual(saved['actor'], 'alice')
        self.assertEqual(saved['scope_version'], 1)
        self.assertIsNone(saved['approval'])
        self.assertIsNone(saved['review'])
        self.assertEqual(saved['argv'][1:5], ['-I', '-B', '-m', 'unittest'])
        for wrong in ({**self.ctx, 'actor': 'bob'}, {**self.ctx, 'project_id': 'elsewhere'}, {**self.ctx, 'thread_id': 'elsewhere'}):
            with self.assertRaises(Rejected):
                service.read(task['id'], **wrong)
        with self.assertRaisesRegex(Rejected, 'export'):
            service.export(task['id'], **self.ctx)


class CheckBlockerWords(unittest.TestCase):
    def test_a_check_that_couldnt_run_says_so_in_plain_words(self):
        """Polish review, 9 Oct: the blocked card showed "ConfinementBlocked: Confinement unavailable: ..."."""
        import task_evidence
        from dervo_connections import Rejected
        said = task_evidence.check_blocker(task_evidence.ConfinementBlocked('Confinement unavailable: requires macOS sandbox-exec'))
        self.assertEqual(said, task_evidence.CONFINEMENT_PLAIN)
        self.assertNotIn('Confinement', said)
        self.assertEqual(task_evidence.check_blocker(Rejected('Stale file preimage')), 'Stale file preimage')
        self.assertEqual(task_evidence.check_blocker(ValueError('x')), 'The check couldn’t run (ValueError: x)')


if __name__ == '__main__':
    unittest.main()


class RustTaskIntegrationTests(unittest.TestCase):
    setUp = TaskEvidenceTests.setUp
    service = TaskEvidenceTests.service

    @unittest.skipUnless(sys.platform == 'darwin', 'Rust checks run only on a Mac')
    def test_rust_scope_snapshot_launch_and_cancel(self):
        from unittest.mock import patch
        import rust_checks
        import task_evidence
        import threading
        import time
        (self.root / 'check.rs').write_text('pub fn ok() {}')
        identity = dict(root='/trusted/rust', target='aarch64-apple-darwin', files={})
        with patch.object(rust_checks, 'toolchain_root', return_value=Path('/trusted/rust')), patch.object(rust_checks, 'identity', return_value=identity):
            service = self.service()
            task = service.create(files=['check.rs'], test_file='check.rs', check_profile='rust-check', **self.ctx)
            self.assertEqual(task['compiler'], identity)
            service.approve(task['id'], scope_version=1, **self.ctx)
            prepared = service.verify_current(task['id'], **self.ctx)
            self.assertEqual(prepared['before'], prepared['after'])
            self.assertEqual(prepared['diff'], {})
            with patch.object(rust_checks, 'identity', return_value={**identity, 'files': {'changed': {}}}):
                with self.assertRaisesRegex(Rejected, 'changed'):
                    service.launch(task['id'], attempt_number=1, **self.ctx)
            self.assertFalse(service.busy())
            entered = threading.Event()
            def verify(payload, snapshots, cancel, compiler):
                self.assertEqual(compiler['identity'], identity)
                self.assertEqual(payload['check_profile'], 'rust-check')
                self.assertEqual(snapshots, prepared['after'])
                entered.set()
                cancel.wait(2)
                return dict(exit_status=0, stopped='cancelled', stdout='', stderr='', check_profile='rust-check')
            with patch.object(task_evidence.TaskEvidence, '_verify', side_effect=verify):
                job = service.launch(task['id'], attempt_number=1, **self.ctx)
                self.assertTrue(entered.wait(2))
                service.cancel(task['id'], job_id=job, **self.ctx)
                deadline = time.monotonic() + 2
                while True:
                    attempt = service.poll(task['id'], job_id=job, **self.ctx)
                    if attempt['state'] != 'running':
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
            self.assertEqual(attempt['state'], 'cancelled')
            with self.assertRaises(Rejected):
                service.review(task['id'], attempt_number=1, decision='approved', **self.ctx)
            self.assertEqual((self.root / 'check.rs').read_text(), 'pub fn ok() {}')


class BigFileTests(unittest.TestCase):
    """Big files in scoped mode (beta.15): harrison's 24 Sep report of files over 256 KiB a worker couldn't open."""
    setUp = TaskEvidenceTests.setUp
    service = TaskEvidenceTests.service

    def task(self, service, data):
        (self.root / 'big.txt').write_bytes(data)
        task = service.create(files=['big.txt', 'test_sample.py'], test_file='test_sample.py', **self.ctx)
        service.approve(task['id'], scope_version=1, **self.ctx)
        return task

    def test_a_big_file_is_read_in_parts_and_its_before_copy_kept_beside_the_database(self):
        import hashlib
        service = self.service()
        data = ''.join(f'row {n}\n' for n in range(1, 60001)).encode()
        task = self.task(service, data)
        snap = service.read_file(task['id'], 'big.txt', **self.ctx)
        self.assertEqual(snap, dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), stored=True))
        kept = self.base / 'file-versions' / snap['sha256'][:2] / snap['sha256']
        self.assertEqual(kept.read_bytes(), data)
        if sys.platform != 'win32':  # Windows has no permission bits; the folder is in the user's own app data
            self.assertEqual(kept.stat().st_mode & 0o777, 0o600)
        whole = service.read_lines(snap)
        self.assertEqual((whole['start_line'], whole['total_lines'], whole['partial']), (1, 60000, True))
        self.assertLessEqual(len(whole['content'].encode()), 40000)
        self.assertEqual(whole['next'], f'Lines 1 to {whole["end_line"]} of 60000. Read on with start_line {whole["end_line"] + 1}.')
        last = service.read_lines(snap, 59999, 70000)
        self.assertEqual((last['content'], last['end_line']), ('row 59999\nrow 60000\n', 60000))
        self.assertNotIn('partial', last)
        with self.assertRaisesRegex(Rejected, '60000 lines'):
            service.read_lines(snap, 60001)
        small = service.read_file(task['id'], 'test_sample.py', **self.ctx)
        self.assertIn('bytes_b64', small, 'a file up to 256 KiB is kept inline, as before')
        self.assertEqual(service.read_lines(small), dict(content=(self.root / 'test_sample.py').read_bytes().decode(), sha256=small['sha256'], total_lines=3))  # the file's own line endings: CRLF on Windows

    def test_an_edit_needs_the_current_hash_and_text_that_occurs_once(self):
        import hashlib
        service = self.service()
        data = ''.join(f'row {n}\n' for n in range(1, 60001)).encode() + b'twice\ntwice\n'
        task = self.task(service, data)
        digest = hashlib.sha256(data).hexdigest()
        for old, why in ((b'row 0\n', 'isn’t in the file'), (b'twice\n', 'occurs 2 times'), (b'', 'exact text')):
            with self.subTest(old=old):
                with self.assertRaisesRegex(Rejected, why):
                    service.edit_file(task['id'], 'big.txt', expected_sha256=digest, old=old, new=b'x', **self.ctx)
        with self.assertRaisesRegex(Rejected, 'Stale file preimage'):
            service.edit_file(task['id'], 'big.txt', expected_sha256='0' * 64, old=b'row 7\n', new=b'row seven\n', **self.ctx)
        self.assertEqual(service.read(task['id'], **self.ctx)['attempts'], [], 'a refused edit records nothing')
        self.assertEqual((self.root / 'big.txt').read_bytes(), data)
        attempt = service.edit_file(task['id'], 'big.txt', expected_sha256=digest, old=b'row 7\n', new=b'row seven\n', **self.ctx)
        self.assertEqual((self.root / 'big.txt').read_bytes(), data.replace(b'row 7\n', b'row seven\n', 1))
        self.assertEqual(attempt['diff']['big.txt'], '--- before/big.txt\n+++ after/big.txt\n@@ -4,7 +4,7 @@\n row 4\n row 5\n row 6\n'
                                                     '-row 7\n+row seven\n row 8\n row 9\n row 10\n')
        self.assertEqual(service.snapshot_bytes(attempt['before']['big.txt']), data)
        with self.assertRaisesRegex(Rejected, 'Stale file preimage'):
            service.edit_file(task['id'], 'big.txt', expected_sha256=digest, old=b'row 8\n', new=b'row eight\n', **self.ctx)

    def test_deleting_the_work_removes_its_kept_copies_but_not_ones_another_task_names(self):
        # Security review S3 (24 Sep): kept versions outlived their project, and file-versions itself took the umask.
        import project_lifecycle
        service = self.service()
        data = ''.join(f'row {n}\n' for n in range(1, 60001)).encode()
        gone = self.task(service, data)
        snap = service.verify_current(gone['id'], **self.ctx)['before']['big.txt']  # the record now names the kept copy
        shared_data = b'shared\n' * 60000
        (self.root / 'shared.txt').write_bytes(shared_data)
        both = [service.create(files=['shared.txt', 'test_sample.py'], test_file='test_sample.py', **self.ctx) for _ in range(2)]
        for t in both:
            service.approve(t['id'], scope_version=1, **self.ctx)
        shared = [service.verify_current(t['id'], **self.ctx)['before']['shared.txt'] for t in both][0]
        folder = self.base / 'file-versions'
        if sys.platform != 'win32':  # as above
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual((folder / snap['sha256'][:2]).stat().st_mode & 0o777, 0o700)
        kept = project_lifecycle.kept_versions(self.store.db, {gone['id'], both[0]['id']})
        self.assertEqual(kept, {snap['sha256'], shared['sha256']})
        with self.store.db:
            self.store.db.execute('DELETE FROM task_evidence WHERE id IN (?, ?)', (gone['id'], both[0]['id']))
        project_lifecycle.forget_kept(self.store.db, kept)
        self.assertFalse((folder / snap['sha256'][:2] / snap['sha256']).exists(), 'the deleted work’s copy is gone')
        self.assertEqual(service.snapshot_bytes(shared), shared_data, 'a copy another task still names stays')
        # Negative control: nothing named, nothing removed.
        self.assertEqual(__import__('task_evidence').forget_versions(self.store.db, set()), 0)

    def test_nothing_over_16_mib_and_a_changed_kept_copy_is_never_used(self):
        from unittest.mock import patch
        import task_evidence
        service = self.service()
        data = b'y\n' * 200000
        task = self.task(service, data)
        with patch.object(task_evidence, 'FILE_LIMIT', 300000):
            with self.assertRaisesRegex(Rejected, '16 MiB'):
                service.read_file(task['id'], 'big.txt', **self.ctx)
        snap = service.read_file(task['id'], 'big.txt', **self.ctx)
        kept = self.base / 'file-versions' / snap['sha256'][:2] / snap['sha256']
        kept.write_bytes(data + b'z')
        with self.assertRaisesRegex(Rejected, 'changed on disk'):
            service.snapshot_bytes(snap)
        kept.unlink()
        with self.assertRaisesRegex(Rejected, 'missing'):
            service.snapshot_bytes(snap)

    def test_a_big_binary_change_keeps_no_hex_diff_and_the_check_gets_the_bytes(self):
        from unittest.mock import patch
        service = self.service()
        data = bytes(range(256)) * 2048
        task = self.task(service, data)
        pre = service.read_file(task['id'], 'big.txt', **self.ctx)
        attempt = service.prepare(task['id'], changes={'big.txt': dict(expected_sha256=pre['sha256'], content=data[:-1] + b'!')}, **self.ctx)
        self.assertTrue(attempt['diff']['big.txt'].startswith('Binary diff not kept for a file over 256 KiB'))
        seen = {}

        def verify(inputs, snapshots, cancel=None, compiler=None):
            import base64
            seen.update({path: base64.b64decode(s['bytes_b64']) for path, s in snapshots.items()})
            return dict(exit_status=0, stopped=None, stdout='', stderr='')
        with patch('task_evidence.TaskEvidence._verify', staticmethod(verify)):
            job = service.launch(task['id'], attempt_number=attempt['number'], **self.ctx)
            import time
            for _ in range(200):
                if service.poll(task['id'], job_id=job, **self.ctx)['state'] != 'running':
                    break
                time.sleep(.01)
        self.assertEqual(seen['big.txt'], data[:-1] + b'!', 'the confined check gets the file itself, read back from the kept copy')
