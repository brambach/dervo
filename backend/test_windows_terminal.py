"""The project terminal on Windows: PowerShell in a ConPTY console in its own Job Object (platform_os.WindowsConsole,
b16/windows milestone 4). Windows only. The Windows build workflow runs it beside the run-path tests, and
test_project_terminal covers the Mac's pty. No account, network or model call."""
import base64
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import platform_os

WAIT = 30  # PowerShell's first start on a busy runner can take several seconds


@unittest.skipUnless(platform_os.WINDOWS, 'ConPTY is Windows only. test_project_terminal covers the Mac pty.')
class WindowsTerminalTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('account_config.create', lambda *args, **kwargs: None))
        from host import Host
        temp = tempfile.TemporaryDirectory(prefix='dervo-terminal-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        self.host = Host(self.root / 'data')
        self.addCleanup(self.host.close)
        self.host.store.add_profile('account', 'claude', self.root / 'profile', 'fixture@example.test', 'personal')
        (self.root / 'one').mkdir()
        self.host.store.add_project('one', self.root / 'one', 'account', 'fixture-model')

    def open(self, client='open-1', cols=80, rows=24):
        row = self.host.dispatch('terminal_open', dict(projectId='one', clientId=client, cols=cols, rows=rows))
        self.wait(lambda: b'>' in self.output(row), 'the shell never showed its prompt')
        return row

    def session(self, row):
        return self.host.terminals.sessions[row['sessionId']]

    def output(self, row):
        self.host.terminals.tick()
        return bytes(self.session(row).output)

    def wait(self, condition, what):
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            self.host.terminals.tick()
            result = condition()
            if result:
                return result
            time.sleep(.05)
        self.fail(f'{what} within {WAIT} s. Output so far: {bytes(next(iter(self.host.terminals.sessions.values())).output)[-600:]!r}')

    def send(self, row, text):
        raw = text.encode()
        return self.host.dispatch('terminal_write', dict(projectId='one', sessionId=row['sessionId'],
                                                         sequence=self.session(row).sequence,
                                                         dataBase64=base64.b64encode(raw).decode()))

    def test_powershell_opens_in_the_project_folder_and_runs_what_you_type_without_the_hosts_secrets(self):
        with patch.dict(os.environ, {'DERVO_FIXTURE_SECRET': 'PRIVATE-FIXTURE'}):
            row = self.open()
        self.assertEqual(list((self.root / 'one').iterdir()), [], 'nothing runs before you type')
        self.send(row, "Write-Output ('dervo' + '-ok'); [IO.File]::WriteAllText('where.txt', (Get-Location).Path); "
                       "[IO.File]::WriteAllText('env.txt', ((Get-ChildItem env: | ForEach-Object { $_.Name + '=' + $_.Value }) -join \"`n\"))\r")
        self.wait(lambda: b'dervo-ok' in self.output(row) and (self.root / 'one' / 'env.txt').exists(), 'the command never ran')
        self.assertEqual((self.root / 'one' / 'where.txt').read_text(encoding='utf-8').lower(), str(self.root / 'one').lower())
        env = (self.root / 'one' / 'env.txt').read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE-FIXTURE', env, "the host's own environment never reaches the shell")
        self.assertIn('TERM_PROGRAM=Dervo', env)
        self.assertRegex(env, r'(?im)^path=.*system32', "the account's own PATH, as a new PowerShell window gets it")

    def test_resize_names_the_running_command_and_close_ends_everything_the_shell_started(self):
        row = self.open()
        resized = self.host.dispatch('terminal_resize', dict(projectId='one', sessionId=row['sessionId'], cols=100, rows=30))
        self.assertEqual((resized['cols'], resized['rows']), (100, 30))
        self.send(row, 'ping -n 120 127.0.0.1\r')
        self.wait(lambda: (self.session(row).foreground() or '').lower() == 'ping.exe', 'the running command was never named')
        console = self.session(row).console
        pids = console.processes()
        self.assertIn(console.pid, pids)
        self.assertGreaterEqual(len(pids), 2, 'the shell and ping')
        closed = self.host.dispatch('terminal_close', dict(projectId='one', sessionId=row['sessionId']))
        self.assertIn(closed['state'], ('closing', 'closed'))
        self.wait(lambda: self.session(row).state == 'closed', 'the terminal never finished closing')
        for pid in pids:
            with self.assertRaises(ProcessLookupError, msg=f'process {pid} outlived Close'):
                platform_os._windows_probe(pid)

    def test_typing_exit_ends_the_session_and_its_output_stays_readable(self):
        row = self.open()
        self.send(row, 'exit\r')
        self.wait(lambda: self.session(row).state == 'exited', 'exit never ended the session')
        read = self.host.dispatch('terminal_read', dict(projectId='one', sessionId=row['sessionId'], cursor=0))
        self.assertIn(b'>', base64.b64decode(read['dataBase64']))
        with self.assertRaises(Exception):
            self.send(row, 'dir\r')


if __name__ == '__main__':
    unittest.main()
