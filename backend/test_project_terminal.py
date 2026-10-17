"""Real local PTYs in disposable projects. No providers, account homes or network."""
import base64
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dervo_connections import Rejected
from host import Host
import project_terminal as m


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dervo-terminal-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.account = patch.object(m.pwd, 'getpwuid', return_value=SimpleNamespace(
            pw_shell='/bin/sh', pw_dir=str(self.home), pw_name='terminal-fixture'))
        self.account.start()
        self.addCleanup(self.account.stop)
        self.host = Host(self.root / 'data')
        self.addCleanup(self.host.close)
        self.host.store.add_profile('account', 'codex', self.root/'profile', 'fixture@example.test', 'personal')
        for pid in ('one', 'two', 'three', 'four', 'five'):
            folder = self.root / pid
            folder.mkdir()
            self.host.store.add_project(pid, folder, 'account', 'fixture-model')

    def open(self, pid='one', client='open-1', cols=80, rows=24):
        result = self.host.dispatch('terminal_open', dict(projectId=pid, clientId=client, cols=cols, rows=rows))
        self.wait(lambda: b'$' in self.output(result) or b'#' in self.output(result))
        return result

    def session(self, row):
        return self.host.terminals.sessions[row['sessionId']]

    def output(self, row):
        self.host.terminals.tick()
        return bytes(self.session(row).output)

    def wait(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.host.terminals.tick()
            result = condition()
            if result:
                return result
            time.sleep(.01)
        self.fail('The disposable terminal did not reach its expected state.')

    def send(self, row, raw, sequence=None):
        if isinstance(raw, str):
            raw = raw.encode()
        return self.host.dispatch('terminal_write', dict(projectId=row['projectId'], sessionId=row['sessionId'],
            sequence=self.session(row).sequence if sequence is None else sequence, dataBase64=base64.b64encode(raw).decode()))

    def close(self, row):
        return self.host.dispatch('terminal_close', dict(projectId=row['projectId'], sessionId=row['sessionId']))

    def test_project_cwd_tty_sanitized_environment_and_no_automatic_command(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'PRIVATE-FIXTURE', 'CODEX_HOME': '/private/fixture',
                                     'DERVO_KEYCHAIN_FD': '50', 'CUSTOM_SECRET': 'PRIVATE-FIXTURE'}):
            row = self.open()
        self.assertEqual(list((self.root/'one').iterdir()), [])
        self.assertEqual(self.session(row).sequence, 0)
        self.send(row, "pwd > result; test -t 0 && test -t 1 && test -t 2 && echo tty >> result; /usr/bin/env > environment\n")
        self.wait(lambda: (self.root/'one'/'environment').exists())
        self.assertEqual((self.root/'one'/'result').read_text().splitlines(), [str(self.root/'one'), 'tty'])
        env = (self.root/'one'/'environment').read_text()
        self.assertIn('TERM=xterm-256color', env)
        self.assertIn('HOME='+str(self.home), env)
        self.assertNotIn('PRIVATE-FIXTURE', env)
        self.assertNotIn('CODEX_HOME=', env)
        self.assertNotIn('KEYCHAIN', env)
        self.assertNotIn(b'no job control', self.output(row))

    def test_resize_ansi_and_split_unicode_bytes(self):
        row = self.open(cols=91, rows=31)
        self.host.dispatch('terminal_resize', dict(projectId='one', sessionId=row['sessionId'], cols=111, rows=42))
        self.send(row, "stty size > size; printf '\\033[31mred\\033[0m\\n'\n")
        # Redirection creates the file before stty writes. This output follows its exit.
        self.wait(lambda: b'\x1b[31mred\x1b[0m' in self.output(row))
        self.assertEqual((self.root/'one'/'size').read_text().strip(), '42 111')
        session = self.session(row)
        cursor = session.end
        for raw in (b'\xf0', b'\x9f\x98', b'\x80'):
            session.append(raw)
        result = self.host.dispatch('terminal_read', dict(projectId='one', sessionId=row['sessionId'], cursor=cursor))
        self.assertEqual(base64.b64decode(result['dataBase64']), '😀'.encode())
        self.assertEqual(result['nextCursor'], cursor+4)
        # Actual PTY writes split one UTF-8 character across separate kernel reads.
        self.send(row, "printf '\\360'; sleep .05; printf '\\237\\230\\200\\n'\n")
        self.wait(lambda: '😀'.encode()+b'\r\n' in self.output(row))

    def test_reused_root_pid_is_not_adopted(self):
        current = {'start': 20, 'ppid': 10, 'exe': '/fixture/other', 'pgid': 30}
        with patch.object(m, 'identity', return_value=current), patch.object(m.subprocess, 'run') as ps:
            ps.return_value.stdout = '30 10\n31 30\n'
            self.assertEqual(m.owned_processes([30], {30: {'start': 19}}), {})

    def test_failed_spawn_preserves_closed_scrollback(self):
        row = self.open()
        self.close(row)
        before = self.session(row).metadata()
        with patch.object(m.subprocess, 'Popen', side_effect=OSError('fixture')):
            with self.assertRaises(Rejected):
                self.host.dispatch('terminal_open', dict(projectId='one', clientId='failed-start', cols=80, rows=24))
        self.assertEqual(self.host.dispatch('terminal_list', {'projectId': 'one'})['sessions'], [before])

    def test_ctrl_c_interrupts_foreground_job_and_shell_accepts_next_command(self):
        row = self.open()
        self.send(row, 'sleep 30\n')
        self.wait(lambda: len(m.owned_processes([self.session(row).process.pid])) > 1)
        self.send(row, b'\x03')
        self.send(row, 'echo resumed > resumed\n')
        self.wait(lambda: (self.root/'one'/'resumed').exists())
        self.assertEqual(self.session(row).state, 'running')

    def test_open_reattach_close_and_new_session_do_not_replay_commands(self):
        row = self.open()
        same = self.host.dispatch('terminal_open', dict(projectId='one', clientId='open-1', cols=80, rows=24))
        alias = self.host.dispatch('terminal_open', dict(projectId='one', clientId='view-remount', cols=80, rows=24))
        self.assertEqual(row['sessionId'], same['sessionId'])
        self.assertEqual(row['sessionId'], alias['sessionId'])
        self.send(row, 'echo once >> count\n')
        self.wait(lambda: (self.root/'one'/'count').exists())
        listed = self.host.dispatch('terminal_list', {'projectId': 'one'})['sessions'][0]
        self.assertEqual(listed['nextInputSeq'], 1)
        self.assertGreater(listed['endCursor'], 0)
        self.assertEqual(self.close(row)['state'], 'closed')
        self.assertEqual(self.host.dispatch('terminal_open', dict(projectId='one', clientId='open-1', cols=80, rows=24))['state'], 'closed')
        new = self.open(client='new-shell')
        self.assertNotEqual(new['sessionId'], row['sessionId'])
        self.assertEqual((self.root/'one'/'count').read_text(), 'once\n')
        with self.assertRaises(Rejected):
            self.host.dispatch('terminal_open', dict(projectId='one', clientId='open-1', cols=80, rows=24))

    def test_input_retry_is_exactly_once_and_rejects_changed_gap_or_stale(self):
        row = self.open()
        raw = 'echo one >> once\n'
        first = self.send(row, raw, 0)
        again = self.send(row, raw, 0)
        self.assertEqual(first['nextInputSeq'], again['nextInputSeq'])
        with self.assertRaises(Rejected):
            self.send(row, 'echo changed\n', 0)
        with self.assertRaises(Rejected):
            self.send(row, 'echo gap\n', 2)
        self.wait(lambda: (self.root/'one'/'once').exists())
        self.assertEqual((self.root/'one'/'once').read_text(), 'one\n')
        self.session(row).replays.clear()
        with self.assertRaises(Rejected):
            self.send(row, raw, 0)

    def test_strict_fields_base64_types_and_input_bounds(self):
        row = self.open()
        for encoded in ('!', 'YQ==\n', 'YR==', '', 'é', 'A'*22000):
            with self.subTest(encoded=encoded[:10]), self.assertRaises(Rejected):
                self.host.dispatch('terminal_write', dict(projectId='one', sessionId=row['sessionId'], sequence=0, dataBase64=encoded))
        for fields in ({'cwd': '/tmp'}, {'env': {}}, {'argv': ['pwd']}):
            with self.assertRaises(Rejected):
                self.host.dispatch('terminal_open', dict(projectId='one', clientId='bad', cols=80, rows=24, **fields))
        for cols, rows in ((True, 24), (1, 24), (501, 24), (80, 0), (80, 301)):
            with self.assertRaises(Rejected):
                self.host.dispatch('terminal_resize', dict(projectId='one', sessionId=row['sessionId'], cols=cols, rows=rows))
        session = self.session(row)
        for _ in range(4):
            self.send(row, b'x'*m.WRITE_LIMIT)
        self.assertEqual(len(session.pending), m.INPUT_LIMIT)
        with self.assertRaises(Rejected):
            self.send(row, b'x')
        self.assertEqual(session.sequence, 4)

    def test_output_ring_read_limit_and_gap_are_explicit(self):
        row = self.open()
        session = self.session(row)
        session.append(b'z'*(m.OUTPUT_LIMIT+100))
        self.assertEqual(len(session.output), m.OUTPUT_LIMIT)
        result = session.read(0)
        self.assertGreaterEqual(result['dropped'], 100)
        self.assertEqual(result['dropped'], result['startCursor'])
        self.assertEqual(len(base64.b64decode(result['dataBase64'])), m.READ_LIMIT)
        self.assertEqual(result['nextCursor'], result['startCursor']+m.READ_LIMIT)
        for cursor in (-1, True, session.end+1):
            with self.assertRaises(Rejected):
                session.read(cursor)

    def test_project_actor_and_beta_guards_leave_close_available(self):
        row = self.open()
        for operation, extra in (('terminal_read', {'cursor': 0}), ('terminal_close', {}),
                                 ('terminal_resize', {'cols': 80, 'rows': 24})): 
            with self.assertRaises(Rejected):
                self.host.dispatch(operation, dict(projectId='two', sessionId=row['sessionId'], **extra))
        with self.assertRaises(Rejected):
            self.host.terminals.dispatch('terminal_read', dict(projectId='one', sessionId=row['sessionId'], cursor=0), 'other-user')
        self.host.require_beta_access = Mock(side_effect=Rejected('Beta access required'))
        with self.assertRaises(Rejected):
            self.send(row, 'pwd\n')
        with self.assertRaises(Rejected):
            self.host.dispatch('terminal_open', dict(projectId='two', clientId='beta', cols=80, rows=24))
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO project_archives VALUES (?,?)', ('one', 1))
        self.assertEqual(len(self.host.dispatch('terminal_list', {'projectId': 'one'})['sessions']), 1)
        self.host.dispatch('terminal_read', dict(projectId='one', sessionId=row['sessionId'], cursor=0))
        self.assertEqual(self.close(row)['state'], 'closed')

    def test_session_cap_and_closed_buffer_eviction(self):
        rows = [self.open(pid) for pid in ('one', 'two', 'three', 'four')]
        with self.assertRaises(Rejected):
            self.host.dispatch('terminal_open', dict(projectId='five', clientId='fifth', cols=80, rows=24))
        self.close(rows[0])
        self.open('five', 'fifth')
        self.assertEqual(len(self.host.terminals.sessions), 4)
        with self.assertRaises(Rejected):
            self.host.dispatch('terminal_read', dict(projectId='one', sessionId=rows[0]['sessionId'], cursor=0))

    def test_close_kills_foreground_and_background_jobs_without_other_terminal(self):
        one, two = self.open(), self.open('two')
        self.send(one, 'sleep 30 & echo $! > background; sleep 30\n')
        self.wait(lambda: (self.root/'one'/'background').exists())
        self.wait(lambda: len(m.owned_processes([self.session(one).process.pid])) >= 3)
        owned = m.owned_processes([self.session(one).process.pid])
        self.assertEqual(self.close(one)['state'], 'closed')
        self.assertFalse(any(m.still_owned(pid, saved) for pid, saved in owned.items()))
        self.assertIsNone(self.session(two).process.poll())
        self.send(two, 'echo alive > alive\n')
        self.wait(lambda: (self.root/'two'/'alive').exists())

    def test_shell_exit_cleans_remembered_separate_session_child(self):
        row = self.open()
        # The child starts another session and ignores HUP. Closing only the shell
        # group would leave it running after the shell exits.
        script = self.root/'one'/'child.py'
        script.write_text('import os,signal,time\nfrom pathlib import Path\nif os.fork() == 0:\n os.setsid()\n signal.signal(signal.SIGHUP,signal.SIG_IGN)\n Path("child-pid").write_text(str(os.getpid()))\ntime.sleep(30)\n')
        self.send(row, f'{os.sys.executable} child.py &\n')
        self.wait(lambda: (self.root/'one'/'child-pid').exists())
        child = int((self.root/'one'/'child-pid').read_text())
        saved = m.identity(child)
        self.host.terminals.remember([self.session(row)])
        self.send(row, 'exit\n')
        self.wait(lambda: self.session(row).state == 'exited')
        self.assertFalse(m.still_owned(child, saved))

    def test_host_shutdown_cleans_all_sessions_with_one_budget(self):
        rows = [self.open(pid) for pid in ('one', 'two', 'three', 'four')]
        for row in rows:
            self.send(row, 'sleep 30\n')
        self.wait(lambda: all(len(m.owned_processes([self.session(row).process.pid])) >= 2 for row in rows))
        owned = m.owned_processes([self.session(row).process.pid for row in rows])
        started = time.monotonic()
        self.host.close()
        self.assertLess(time.monotonic()-started, 2)
        self.assertFalse(any(m.still_owned(pid, saved) for pid, saved in owned.items()))
        self.assertTrue(all(self.session(row).state == 'closed' for row in rows))

    def test_denied_child_cleanup_keeps_lifecycle_hold_until_retry(self):
        row = self.open()
        script = self.root/'one'/'ignore.py'
        script.write_text('import os,signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGHUP,signal.SIG_IGN)\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath("ready").write_text(str(os.getpid()))\ntime.sleep(30)\n')
        self.send(row, f'{os.sys.executable} ignore.py\n')
        self.wait(lambda: (self.root/'one'/'ready').exists())
        with patch.object(m.os, 'kill', side_effect=PermissionError('fixture denial')):
            self.assertEqual(self.close(row)['state'], 'closing')
        self.assertTrue(self.host.terminals.active('one'))
        with self.assertRaises(Rejected):
            self.host.dispatch('project_archive', {'projectId': 'one'})
        self.assertEqual(self.close(row)['state'], 'closed')

    def test_lifecycle_guards_and_blocker_navigation_include_terminal(self):
        from release_switch import require_idle
        row = self.open()
        # Archive waits for the terminal. Delete closes it itself since 23 Sep (app #12, project_lifecycle.stop_project_work),
        # so it isn't checked here.
        with self.assertRaisesRegex(Rejected, 'terminal'):
            self.host.dispatch('project_archive', {'projectId': 'one'})
        with self.assertRaisesRegex(Rejected, 'terminal'):
            require_idle(self.host)
        blockers = self.host.dispatch('lifecycle_blockers', dict(scope='release', projectId=None, channelId=None))
        terminal = next(item for item in blockers if item['kind'] == 'terminal')
        self.assertEqual(terminal['projectId'], 'one')
        self.assertEqual(terminal['threadName'], 'Project terminal')
        self.assertIsNone(terminal['channelId'])
        self.assertIsNone(terminal['taskId'])
        self.close(row)
        require_idle(self.host)
        self.host.dispatch('project_archive', {'projectId': 'one'})


if __name__ == '__main__':
    unittest.main()
