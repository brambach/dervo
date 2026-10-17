"""The bubblewrap command and seccomp filter Linux checks run under (linux_confinement.py). The canary in
test_task_evidence proves the sandbox on a real Linux; these check what's built, on any computer."""
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import linux_confinement as confinement


class Filter(unittest.TestCase):
    def ops(self, machine):
        program = confinement.seccomp_filter(machine)
        self.assertEqual(len(program) % 8, 0)
        return [struct.unpack('<HBBI', program[i:i + 8]) for i in range(0, len(program), 8)]

    def test_each_cpu_checks_its_arch_first_and_allows_last(self):
        for machine, arch in (('x86_64', 0xc000003e), ('aarch64', 0xc00000b7)):
            ops = self.ops(machine)
            self.assertEqual(ops[0][3], 4, 'loads seccomp_data.arch')
            self.assertEqual(ops[1][3], arch)
            self.assertEqual(ops[2][3], 0x80000000, 'another arch kills the process')
            self.assertEqual(ops[-1], (0x06, 0, 0, 0x7fff0000))

    def test_fork_vfork_and_new_processes_are_refused_on_x86(self):
        returns = [op[3] for op in self.ops('x86_64') if op[0] == 0x06]
        self.assertIn(0x00050000 | 1, returns, 'EPERM')
        self.assertIn(0x00050000 | 38, returns, 'clone3 answers ENOSYS')
        compared = {op[3] for op in self.ops('x86_64') if op[0] == 0x15}
        self.assertTrue({57, 58, 56, 435, 41} <= compared)

    def test_an_unknown_cpu_is_unavailable_not_unfiltered(self):
        with self.assertRaises(confinement.Unavailable):
            confinement.seccomp_filter('riscv64')


class Command(unittest.TestCase):
    def test_everything_is_read_only_cleared_and_unshared(self):
        with patch.object(confinement, 'bwrap', return_value='/usr/bin/bwrap'):
            argv = confinement.command(['python3', '-c', 'pass'], '/tmp', '/opt/dervo-python', 7)
        self.assertEqual(argv[:6], ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
                                    '--setenv'])
        self.assertNotIn('--bind', argv, 'nothing is mounted writable')
        self.assertIn('/opt/dervo-python', argv)
        self.assertEqual(argv[argv.index('--seccomp') + 1], '7')
        self.assertLess(argv.index('--remount-ro'), argv.index('--seccomp'))
        self.assertEqual(argv[-4:], ['--', 'python3', '-c', 'pass'])

    def test_a_refused_namespace_is_told_apart_from_a_broken_check(self):
        self.assertTrue(confinement.namespace_refused('bwrap: setting up uid map: Permission denied'))
        self.assertTrue(confinement.namespace_refused('bwrap: No permissions to create new namespace, likely because'))
        self.assertTrue(confinement.namespace_refused('bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted'))
        self.assertTrue(confinement.namespace_refused('bwrap: Failed to make / slave: Permission denied'))
        self.assertFalse(confinement.namespace_refused("bwrap: Can't find source path /missing: No such file"))



@unittest.skipUnless(sys.platform.startswith('linux'), 'reads /proc')
class Settle(unittest.TestCase):
    def test_whatever_still_works_in_the_folder_is_waited_for_then_killed(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            inside = subprocess.Popen(['sleep', '30'], cwd=root)
            outside = subprocess.Popen(['sleep', '30'], cwd=elsewhere)
            try:
                self.assertEqual(confinement.settle(root, seconds=0.2), [inside.pid])
                self.assertEqual(inside.wait(timeout=5), -9)
                self.assertIsNone(outside.poll())
                self.assertEqual(confinement.settle(root, seconds=0.2), [])
            finally:
                for child in (inside, outside):
                    child.kill(); child.wait()


if __name__ == '__main__':
    unittest.main()
