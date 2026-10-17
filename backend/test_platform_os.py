"""platform_os on a Mac does exactly what its call sites did before it existed (b16/seam), and nothing else in the
backend reaches the process-group, pty or lock calls directly."""
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

import platform_os

BACKEND = Path(__file__).resolve().parent


@unittest.skipUnless(sys.platform == 'darwin', 'the Mac values')
class MacValues(unittest.TestCase):
    def test_open_commands_are_the_argv_the_call_sites_used(self):
        target = Path('/tmp/some folder/a file.md')
        self.assertEqual(platform_os.reveal_command(target), ['/usr/bin/open', '-R', str(target)])
        self.assertEqual(platform_os.open_command(target), ['/usr/bin/open', '--', str(target)])
        self.assertEqual(platform_os.open_as_text_command(target), ['/usr/bin/open', '-t', str(target)])
        self.assertEqual(platform_os.open_folder_command(target), ['/usr/bin/open', str(target)])

    def test_app_data_is_application_support_under_home(self):
        with patch('pathlib.Path.home', return_value=Path('/Users/someone')):
            self.assertEqual(platform_os.app_support_dir(), Path('/Users/someone/Library/Application Support'))

    def test_a_new_group_is_a_new_session(self):
        self.assertEqual(platform_os.new_group(), {'start_new_session': True})
        # Negative control: each call is a fresh dict, so one caller can't change another's arguments.
        first = platform_os.new_group()
        first['start_new_session'] = False
        self.assertEqual(platform_os.new_group(), {'start_new_session': True})


@unittest.skipIf(os.name == 'nt', 'POSIX process groups, pty and flock')
class PosixBehaviour(unittest.TestCase):
    def test_signal_group_reaches_the_leader_and_its_group(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **platform_os.new_group())
        try:
            self.assertTrue(platform_os.is_group_leader(child.pid))
            platform_os.signal_group(child.pid, 0)
            platform_os.signal_group(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL)
            # Negative control: the group has gone, and killpg's own error comes through unchanged.
            with self.assertRaises(ProcessLookupError):
                platform_os.signal_group(child.pid, 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_signal_group_goes_through_os_killpg(self):
        # Tests elsewhere patch os.killpg to simulate denials; platform_os must look it up at call time.
        with patch('os.killpg') as killpg:
            platform_os.signal_group(4242, signal.SIGTERM)
        killpg.assert_called_once_with(4242, signal.SIGTERM)

    def test_a_held_lock_refuses_a_second_taker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'x.lock')
            first = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            second = os.open(path, os.O_RDWR)
            try:
                platform_os.lock_nowait(first)
                with self.assertRaises(BlockingIOError):
                    platform_os.lock_nowait(second)
                os.close(first)
                first = None
                platform_os.lock_nowait(second)  # released with its file
            finally:
                for fd in (first, second):
                    if fd is not None:
                        os.close(fd)

    def test_a_pty_opens_and_takes_a_size(self):
        import fcntl
        import struct
        import termios
        controller, terminal = platform_os.open_pty()
        try:
            platform_os.set_pty_size(terminal, 40, 120)
            rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(controller, termios.TIOCGWINSZ, b'\0' * 8))
            self.assertEqual((rows, cols), (40, 120))
        finally:
            os.close(controller)
            os.close(terminal)


class ContainedChild(unittest.TestCase):
    """Runs on both platforms. On a Mac ``contained`` changes nothing; on Windows it's a Job Object."""

    def test_a_mac_contained_child_is_an_ordinary_group(self):
        if os.name == 'nt':
            self.skipTest('the Mac values')
        self.assertEqual(platform_os.new_group(contained=True), platform_os.new_group())
        child = subprocess.Popen([sys.executable, '-c', 'pass'], **platform_os.new_group(contained=True))
        platform_os.contain(child)
        child.wait(timeout=5)
        platform_os.release(child.pid)
        self.assertEqual(platform_os._JOBS, {})

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Objects')
    def test_stopping_ends_a_grandchild_whose_parent_already_exited(self):
        # taskkill /T can't find this grandchild once its parent is gone. The job still holds it.
        start = ('import subprocess, sys; g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); '
                 'print(g.pid, flush=True)')
        child = subprocess.Popen([sys.executable, '-c', start], stdout=subprocess.PIPE,
                                 **platform_os.new_group(contained=True))
        platform_os.contain(child)
        try:
            grandchild = int(child.stdout.readline())
            child.wait(timeout=10)
            platform_os.signal_group(child.pid, 0)  # the leader has gone, the job still runs
            platform_os._windows_probe(grandchild)
            platform_os.signal_group(child.pid, platform_os.SIGKILL)
            for _ in range(50):
                try:
                    platform_os._windows_probe(grandchild)
                except ProcessLookupError:
                    break
                __import__('time').sleep(0.1)
            else:
                self.fail('the grandchild outlived its job')
            with self.assertRaises(ProcessLookupError):
                platform_os.signal_group(child.pid, 0)
        finally:
            platform_os.release(child.pid)
            child.stdout.close()


@unittest.skipUnless(sys.platform == 'darwin', 'the Mac values')
class MacValuesMilestone3(unittest.TestCase):
    def test_provider_environment_is_the_fixed_mac_one(self):
        self.assertEqual(platform_os.provider_environment(),
                         {'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8'})

    def test_git_and_account_come_from_where_they_always_did(self):
        import pwd
        self.assertEqual(platform_os.git_executable(), '/usr/bin/git')
        self.assertEqual(platform_os.account_home(), pwd.getpwuid(os.getuid()).pw_dir)
        self.assertEqual(platform_os.account_name(), pwd.getpwuid(os.getuid()).pw_name)
        self.assertEqual(platform_os.executable_name('claude'), 'claude')
        root = platform_os.scratch_root()
        self.assertNotIn(root, ('/private/tmp', '/tmp'), 'never a folder other accounts can write')
        self.assertEqual(os.stat(root).st_uid, os.getuid())
        self.assertEqual(platform_os.hidden_console(), {})

    def test_walk_calls_use_the_mac_flags(self):
        with patch('os.open', return_value=7) as opened:
            platform_os.open_root('/Users/someone/project')
            platform_os.open_dir_at('src', 3)
            platform_os.open_file_at('a.py', 3)
            platform_os.open_file_at('a.py', 3, 'rw')
            platform_os.open_file_at('new.py', 3, 'new', 0o644)
        directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.assertEqual([c.args for c in opened.call_args_list], [
            ('/', directory), ('src', directory),
            ('a.py', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), ('a.py', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK),
            ('new.py', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)])
        self.assertEqual([c.kwargs for c in opened.call_args_list], [{}] + [{'dir_fd': 3}] * 4)
        self.assertEqual(platform_os.below_root('/Users/someone/project'), ('Users', 'someone', 'project'))


@unittest.skipUnless(sys.platform.startswith('linux'), 'the Linux values')
class LinuxValues(unittest.TestCase):
    def test_files_and_folders_open_with_xdg_open(self):
        with tempfile.TemporaryDirectory() as folder:
            file = os.path.join(folder, 'notes.md')
            open(file, 'w').close()
            self.assertEqual(platform_os.open_command(file), ['/usr/bin/xdg-open', file])
            self.assertEqual(platform_os.open_as_text_command(file), ['/usr/bin/xdg-open', file])
            self.assertEqual(platform_os.open_folder_command(folder), ['/usr/bin/xdg-open', folder])
            # No portable "select this file": its folder opens instead.
            self.assertEqual(platform_os.reveal_command(file), ['/usr/bin/xdg-open', folder])
            self.assertEqual(platform_os.reveal_command(folder), ['/usr/bin/xdg-open', folder])

    def test_data_temp_and_provider_environment(self):
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
        with patch.dict(os.environ, {'XDG_DATA_HOME': ''}):
            self.assertEqual(platform_os.app_support_dir(), Path(home) / '.local/share')
        with patch.dict(os.environ, {'XDG_DATA_HOME': '/srv/data'}):
            self.assertEqual(platform_os.app_support_dir(), Path('/srv/data'))
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'relative'}):
            self.assertEqual(platform_os.app_support_dir(), Path(home) / '.local/share', 'a relative XDG path is ignored')
        with patch.dict(os.environ, {'XDG_CACHE_HOME': ''}):
            self.assertEqual(platform_os.scratch_root(), os.path.realpath(os.path.join(home, '.cache', 'dervo', 'scratch')))
        env = platform_os.provider_environment()
        self.assertEqual(env['PATH'].split(':')[0], os.path.join(home, '.local', 'bin'), "Claude Code's own install folder first")
        self.assertEqual(set(env), {'PATH', 'LANG', 'LC_ALL'}, 'nothing from the host')
        self.assertEqual(platform_os.git_executable(), '/usr/bin/git')

    def test_the_app_names_its_own_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = os.path.join(folder, 'dervo-local')
            open(exe, 'w').close()
            with patch.dict(os.environ, {'DERVO_APP_EXECUTABLE': exe}):
                self.assertEqual(platform_os.app_executable('/usr/lib/Dervo Local'), Path(exe))
            for named in ('', 'relative/dervo-local', os.path.join(folder, 'missing')):
                with self.subTest(named=named), patch.dict(os.environ, {'DERVO_APP_EXECUTABLE': named}):
                    with self.assertRaises(RuntimeError):
                        platform_os.app_executable('/usr/lib/Dervo Local')


@unittest.skipIf(os.name == 'nt', 'Mac scratch folder')
class MacScratch(unittest.TestCase):
    """MCP parity review, 9 Oct: Home and connectors-only runs worked in /private/tmp/dervo-claude-*, which every local
    account can write, and npx and uv walk up from there for node_modules, .npmrc and uv.toml."""

    def setUp(self):
        # The Mac branch, whichever system runs the test.
        self.enterContext(patch.object(platform_os, 'LINUX', False))

    def test_the_account_s_own_temp_folder_when_it_s_under_var_folders(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            own = os.path.join(home, 'T')
            os.mkdir(own)
            with patch('tempfile.gettempdir', return_value=own), patch('os.path.realpath', side_effect=lambda p: '/private/var/folders/ab/c/T' if p == own else p), \
                    patch('os.stat', return_value=os.stat_result((0o40700, 0, 0, 0, os.getuid(), 0, 0, 0, 0, 0))):
                self.assertEqual(platform_os.scratch_root(), '/private/var/folders/ab/c/T')

    def test_the_system_s_own_answer_when_the_app_cleared_tmpdir(self):
        # Review of #47: the app starts the host with a cleared environment, so TMPDIR isn't there to read.
        with patch.object(sys, 'platform', 'darwin'), patch.object(os, 'confstr_names', {'CS_DARWIN_USER_TEMP_DIR': 65537}, create=True), \
                patch.object(os, 'confstr', lambda name: '/private/var/folders/ab/c/T/', create=True), \
                patch('tempfile.gettempdir', return_value='/tmp'), \
                patch('os.stat', return_value=os.stat_result((0o40700, 0, 0, 0, os.getuid(), 0, 0, 0, 0, 0))):
            self.assertEqual(platform_os.scratch_root(), '/private/var/folders/ab/c/T')

    def test_otherwise_a_private_folder_in_the_account_s_caches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            with patch.object(sys, 'platform', 'darwin'), patch.object(os, 'confstr_names', {}, create=True), \
                    patch('tempfile.gettempdir', return_value='/tmp'), patch.object(platform_os, 'account_home', return_value=home):
                root = platform_os.scratch_root()
                self.assertEqual(root, os.path.realpath(os.path.join(home, 'Library', 'Caches', 'Dervo', 'scratch')))
                self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
                os.chmod(root, 0o777)
                self.assertEqual(os.stat(platform_os.scratch_root()).st_mode & 0o777, 0o700, 'made private again')

    def test_linux_uses_its_cache_folder_not_a_mac_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            with patch.object(platform_os, 'LINUX', True), patch('tempfile.gettempdir', return_value='/tmp'), \
                    patch.object(platform_os, 'account_home', return_value=home), patch.dict(os.environ, {}, clear=False):
                os.environ.pop('XDG_CACHE_HOME', None)
                self.assertEqual(platform_os.scratch_root(), os.path.realpath(os.path.join(home, '.cache', 'dervo', 'scratch')))
                os.environ['XDG_CACHE_HOME'] = os.path.join(home, 'xdg')
                self.assertEqual(platform_os.scratch_root(), os.path.realpath(os.path.join(home, 'xdg', 'dervo', 'scratch')))
                self.assertFalse(os.path.exists(os.path.join(home, 'Library')))

    def test_windows_uses_a_dervo_folder_in_local_app_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as local:
            with patch.object(platform_os, 'WINDOWS', True), patch.dict(os.environ, {'LOCALAPPDATA': local}):
                self.assertEqual(platform_os.scratch_root(), os.path.realpath(os.path.join(local, 'Dervo', 'scratch')))
                self.assertTrue(os.path.isdir(os.path.join(local, 'Dervo', 'scratch')))
            with patch.object(platform_os, 'WINDOWS', True), patch.dict(os.environ, {'LOCALAPPDATA': ''}), \
                    patch('tempfile.gettempdir', return_value=local):
                self.assertEqual(platform_os.scratch_root(), os.path.realpath(local), "TEMP's real path without LOCALAPPDATA")

    def test_dervo_s_runs_are_never_offered_as_projects_and_their_scratch_repos_work(self):
        import mac_survey
        with patch.object(platform_os, 'scratch_root', return_value='/Users/x/Library/Caches/Dervo/scratch'):
            self.assertEqual(mac_survey._scratch(), ('/Users/x/Library/Caches/Dervo/scratch',))


@unittest.skipUnless(os.name == 'nt', 'Windows temp folder names')
class WindowsScratch(unittest.TestCase):
    def test_a_runs_folder_is_the_long_real_path_claude_code_names_its_session_after(self):
        """The build runner's TEMP is C:\\Users\\RUNNER~1\\...: a run folder there would put Claude Code's session under
        a C--Users-RUNNER-1-... folder, and resume (place_session) would look under the long name instead."""
        import tempfile
        root = platform_os.scratch_root()
        self.assertEqual(root, os.path.realpath(root))
        self.assertNotIn('~', root)
        with tempfile.TemporaryDirectory(prefix='dervo-claude-', dir=root) as run:
            self.assertEqual(run, os.path.realpath(run))


class WalkWithoutLinks(unittest.TestCase):
    """Runs on both platforms: the Windows build workflow runs the backend tests on a Windows runner."""

    def setUp(self):
        import tempfile
        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        (self.folder / 'src').mkdir()
        (self.folder / 'src' / 'a.py').write_bytes(b'print(1)\n')
        (self.folder / 'outside').mkdir()
        (self.folder / 'outside' / 'secret.txt').write_bytes(b'no')

    def walk(self, *parts):
        fds = [platform_os.open_root(self.folder)]
        try:
            for part in platform_os.below_root(self.folder) + parts:
                fds.append(platform_os.open_dir_at(part, fds[-1]))
        except BaseException:
            for fd in reversed(fds):
                os.close(fd)
            raise
        return fds

    def close(self, fds):
        for fd in reversed(fds):
            os.close(fd)

    def link_directory(self, name, target):
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(target), str(self.folder / name))
        else:
            os.symlink(target, self.folder / name)

    def test_a_plain_walk_reads_writes_creates_and_lists(self):
        import stat
        fds = self.walk('src')
        try:
            fd = platform_os.open_file_at('a.py', fds[-1])
            try:
                self.assertEqual(os.read(fd, 100), b'print(1)\n')
                self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            finally:
                os.close(fd)
            fd = platform_os.open_file_at('b.py', fds[-1], 'new')
            try:
                self.assertEqual(os.write(fd, b'x = 2\n'), 6)
                os.fsync(fd)
            finally:
                os.close(fd)
            self.assertEqual((self.folder / 'src' / 'b.py').read_bytes(), b'x = 2\n')
            with self.assertRaises(FileExistsError):
                platform_os.open_file_at('b.py', fds[-1], 'new')
            self.assertEqual(platform_os.stat_at('b.py', fds[-1]).st_size, 6)
            with self.assertRaises(FileNotFoundError):
                platform_os.stat_at('missing.py', fds[-1])
            listing = os.dup(fds[-1])
            try:
                with platform_os.scandir_fd(listing) as it:
                    found = {entry.name: entry.stat(follow_symlinks=False) for entry in it}
            finally:
                os.close(listing)
            self.assertEqual(sorted(found), ['a.py', 'b.py'])
            self.assertEqual(found['a.py'].st_nlink, 1, 'a real link count, which Windows DirEntry.stat leaves at 0')
            info = os.fstat(fds[-1])
            self.assertTrue(stat.S_ISDIR(info.st_mode))
            self.assertEqual([info.st_dev, info.st_ino][1], os.stat(self.folder / 'src').st_ino)
        finally:
            self.close(fds)

    def test_a_linked_folder_is_refused_like_o_nofollow(self):
        self.link_directory('linked', self.folder / 'outside')
        with self.assertRaises(OSError):
            self.close(self.walk('linked'))
        # Negative control: the same folder reached by its own name opens.
        self.close(self.walk('outside'))

    def test_a_download_folder_opens_links_without_replacing_and_unlinks(self):
        # beta_download's calls: open the chosen folder, check free space, publish by hard link, remove the .part.
        fd = platform_os.open_dir(self.folder / 'src')
        try:
            self.assertEqual(os.fstat(fd).st_ino, os.stat(self.folder / 'src').st_ino)
            self.assertGreater(platform_os.free_bytes(fd), 0)
            (self.folder / 'src' / 'b.part').write_bytes(b'new')
            with self.assertRaises(FileExistsError):
                platform_os.link_at('b.part', 'a.py', fd)
            self.assertEqual((self.folder / 'src' / 'a.py').read_bytes(), b'print(1)\n', 'never replaced')
            platform_os.link_at('b.part', 'b.py', fd)
            platform_os.unlink_at('b.part', fd)
            self.assertEqual(sorted(p.name for p in (self.folder / 'src').iterdir()), ['a.py', 'b.py'])
            self.assertEqual((self.folder / 'src' / 'b.py').read_bytes(), b'new')
        finally:
            os.close(fd)
        self.link_directory('linked', self.folder / 'outside')
        with self.assertRaises(OSError):
            os.close(platform_os.open_dir(self.folder / 'linked'))
        with self.assertRaises(NotADirectoryError):
            os.close(platform_os.open_dir(self.folder / 'src' / 'a.py'))

    def test_a_restore_makes_lists_renames_and_removes_inside_an_open_folder(self):
        # handoff_snapshot.restore's and check_profiles' calls, which used dir_fd and os.listdir(fd) before.
        fd = platform_os.open_dir(self.folder, follow=True)
        try:
            platform_os.mkdir_at('made', fd)
            inner = platform_os.open_dir_at('made', fd)
            try:
                out = platform_os.open_file_at('t.tmp', inner, 'new', 0o600)
                try:
                    os.write(out, b'restored\n')
                finally:
                    os.close(out)
                platform_os.replace_at('t.tmp', 'kept.txt', inner)
                self.assertEqual(platform_os.listdir_fd(inner), ['kept.txt'])
                self.assertEqual(platform_os.stat_at('kept.txt', inner).st_size, 9)
                platform_os.unlink_at('kept.txt', inner)
                self.assertEqual(platform_os.listdir_fd(inner), [])
            finally:
                os.close(inner)
            platform_os.rmdir_at('made', fd)
            self.assertEqual(sorted(platform_os.listdir_fd(fd)), ['outside', 'src'])
        finally:
            os.close(fd)

    def test_a_missing_or_file_part_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            self.close(self.walk('nope'))
        with self.assertRaises(NotADirectoryError):
            self.close(self.walk('src', 'a.py'))


class PipeSelector(unittest.TestCase):
    """Runs on both platforms: a child's output arrives through pipe_selector, then its end reads as b''."""

    def test_output_then_end(self):
        import selectors
        child = subprocess.Popen([sys.executable, '-c', 'import sys, time; sys.stdout.write("one\\n"); sys.stdout.flush(); time.sleep(0.3)'],
                                 stdout=subprocess.PIPE)
        selector = platform_os.pipe_selector()
        try:
            selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
            chunks = []
            while selector.get_map():
                events = selector.select(5)
                self.assertTrue(events, 'the child wrote or ended within 5 seconds')
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    chunks.append(chunk)
            self.assertEqual(b''.join(chunks).replace(b'\r\n', b'\n'), b'one\n')
            # Negative control: an empty selector times out empty-handed.
            self.assertEqual(selector.select(0.05), [])
        finally:
            selector.close()
            child.stdout.close()
            child.wait()


class OnlyThroughTheSeam(unittest.TestCase):
    # New process-group, pty and lock code goes through platform_os (PLAN-beta16-build-20260925.md, asks between
    # threads). The vendored harness is checked byte for byte against PROVENANCE.json, so it changes upstream, not here.
    PATTERN = re.compile(r'os\.killpg\(|os\.getpgid\(|start_new_session\s*=\s*True|^\s*import (pty|fcntl|termios)\b', re.M)

    def test_no_backend_module_reaches_them_directly(self):
        found = []
        for path in sorted(BACKEND.glob('*.py')):
            if path.name == 'platform_os.py' or path.name.startswith('test_'):
                continue
            for match in self.PATTERN.finditer(path.read_text(encoding='utf-8')):
                line = path.read_text(encoding='utf-8').count('\n', 0, match.start()) + 1
                found.append(f'{path.name}:{line}: {match.group(0).strip()}')
        self.assertEqual(found, [])

    def test_the_check_catches_a_direct_call(self):
        # Negative control: the pattern does match what it's meant to stop.
        for text in ('os.killpg(pid, 9)', 'x = Popen(a, start_new_session=True)', 'import fcntl', '    import pty'):
            self.assertTrue(self.PATTERN.search(text), text)
        self.assertFalse(self.PATTERN.search('platform_os.signal_group(pid, 9)'))


if __name__ == '__main__':
    unittest.main()
