"""Checks on Linux: the stand-in for macOS sandbox-exec and Windows' app container (windows_confinement.py).

A Python check runs under bubblewrap (the distribution's /usr/bin/bwrap, the sandbox Flatpak uses) in new user, mount,
PID, network, IPC and UTS namespaces, with a seccomp filter loaded before the check's first instruction:

- Reads: the system's /usr and library folders (Python and libc), Dervo's own Python and the check's snapshot, all
  read-only. Not /home, /etc, /tmp, /run, /proc or any other folder: they don't exist inside.
- Writes: none. The sandbox's own root is a tmpfs bwrap remounts read-only before the check starts.
- Network: a new network namespace with only its own loopback, and the seccomp filter refuses any socket that isn't a
  Unix socket (EPERM), so not even that loopback can be used.
- Child processes: the seccomp filter refuses fork, vfork and clone without CLONE_THREAD (EPERM), and clone3 answers
  ENOSYS so libc falls back to the filtered clone. Threads still start, as on a Mac.
- Environment: cleared; HOME is empty and there's no PATH.

The host proves all of it with its Linux canary (task_evidence.TaskEvidence.canary_source) before any project code
runs, as on the other platforms. A missing bwrap, an unknown CPU or a kernel that refuses user namespaces is
Unavailable, which the host turns into ConfinementBlocked, never an unconfined retry.
"""
import os
from pathlib import Path
import platform
import struct

CONFINEMENT = 'linux-bubblewrap'
BWRAP_CANDIDATES = ('/usr/bin/bwrap',)
# Read-only system folders a dynamically linked Python needs: the interpreter's libraries and libc. On a merged-/usr
# distribution /lib and friends are links into /usr; either way they hold no user data.
SYSTEM_FOLDERS = ('/usr', '/lib', '/lib64', '/lib32', '/bin', '/sbin')


class Unavailable(Exception):
    pass


# Ubuntu 23.10 and later refuse user namespaces to programs no AppArmor profile names. The .deb names Dervo
# (packaging/linux/apparmor-dervo-local); an AppImage can't, because it runs from a different folder each time.
NAMESPACE_REFUSED = ('Linux refused the sandbox Dervo runs checks in. On Ubuntu 23.10 or later, install Dervo from '
                     'its .deb package, which lets it start one, or restart after installing it. Nothing ran unconfined.')


def namespace_refused(stderr):
    """True when bwrap's error is the kernel or AppArmor refusing the user namespace, not a problem with the check."""
    # Ubuntu 24.04's restriction lets an unconfined bwrap create the namespace but takes its capabilities inside, so it
    # fails a step later: bringing up the sandbox's loopback, or making / a slave mount.
    return any(text in stderr for text in ('setting up uid map', 'No permissions to create new namespace',
                                           'Creating new namespace failed', 'unshare', 'loopback: Failed RTM_NEWADDR',
                                           'Failed to make / slave'))


def bwrap():
    for candidate in BWRAP_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise Unavailable('bubblewrap (/usr/bin/bwrap) is not installed. Install the bubblewrap package to run checks.')


# seccomp-bpf, assembled here rather than through libseccomp, which isn't in the standard library.
_LD_ABS = 0x20          # BPF_LD | BPF_W | BPF_ABS
_JEQ = 0x15             # BPF_JMP | BPF_JEQ | BPF_K
_JGE = 0x35             # BPF_JMP | BPF_JGE | BPF_K
_JSET = 0x45            # BPF_JMP | BPF_JSET | BPF_K
_RET = 0x06             # BPF_RET | BPF_K
_ALLOW = 0x7fff0000
_KILL = 0x80000000      # SECCOMP_RET_KILL_PROCESS
_EPERM = 0x00050000 | 1
_ENOSYS = 0x00050000 | 38
_CLONE_THREAD = 0x00010000
_AF_UNIX = 1
_X32_BIT = 0x40000000

# AUDIT_ARCH and the syscalls the filter names, per CPU. arm64 has no fork or vfork syscalls.
ARCHES = {
    'x86_64': dict(arch=0xc000003e, fork=57, vfork=58, clone=56, clone3=435, socket=41, x32=True),
    'aarch64': dict(arch=0xc00000b7, fork=None, vfork=None, clone=220, clone3=435, socket=198, x32=False),
}


def _op(code, k, jt=0, jf=0):
    return struct.pack('<HBBI', code, jt, jf, k)


def seccomp_filter(machine=None):
    """The compiled filter bwrap loads with --seccomp: a list of sock_filter structs, little-endian."""
    names = ARCHES.get(machine or platform.machine())
    if names is None:
        raise Unavailable('Checks run on x86-64 and arm64 Linux only.')
    program = [_op(_LD_ABS, 4),                                   # seccomp_data.arch
               _op(_JEQ, names['arch'], 1, 0), _op(_RET, _KILL),
               _op(_LD_ABS, 0)]                                   # seccomp_data.nr
    if names['x32']:
        program += [_op(_JGE, _X32_BIT, 0, 1), _op(_RET, _KILL)]  # the x32 ABI would dodge every rule below
    for name in ('fork', 'vfork'):
        if names[name] is not None:
            program += [_op(_JEQ, names[name], 0, 1), _op(_RET, _EPERM)]
    program += [_op(_JEQ, names['clone3'], 0, 1), _op(_RET, _ENOSYS)]
    # clone: a thread (CLONE_THREAD in the low word of args[0]) is allowed, anything else is a new process.
    program += [_op(_JEQ, names['clone'], 0, 4), _op(_LD_ABS, 16), _op(_JSET, _CLONE_THREAD, 0, 1), _op(_RET, _ALLOW),
                _op(_RET, _EPERM)]
    # socket: only AF_UNIX (args[0], the domain). A non-clone syscall skipped the clone branch with the nr still loaded.
    program += [_op(_JEQ, names['socket'], 0, 4), _op(_LD_ABS, 16), _op(_JEQ, _AF_UNIX, 0, 1), _op(_RET, _ALLOW),
                _op(_RET, _EPERM)]
    program += [_op(_RET, _ALLOW)]
    return b''.join(program)


def command(argv, root, runtime, filter_fd):
    """bwrap's argv for ``argv`` in ``root`` with ``runtime`` (Dervo's Python) readable. ``filter_fd`` is an inherited
    descriptor holding seccomp_filter()."""
    root, runtime = str(Path(root).resolve()), str(Path(runtime).resolve())
    args = [bwrap(), '--unshare-all', '--die-with-parent', '--new-session', '--clearenv', '--setenv', 'HOME', '',
            '--dev', '/dev']
    for folder in SYSTEM_FOLDERS:
        if os.path.lexists(folder):
            if os.path.islink(folder):
                args += ['--symlink', os.readlink(folder), folder]
            else:
                args += ['--ro-bind', folder, folder]
    if not any(runtime == folder or runtime.startswith(folder + '/') for folder in SYSTEM_FOLDERS):
        args += ['--ro-bind', runtime, runtime]
    args += ['--ro-bind', root, root, '--chdir', root, '--remount-ro', '/', '--seccomp', str(filter_fd), '--', *argv]
    return args


def settle(root, seconds=2.0):
    """Wait until nothing still works in ``root`` after the sandbox was killed, and kill what does.

    Killing bwrap ends the sandbox through --die-with-parent and the PID namespace closing, but the kernel does that
    after bwrap itself has been reaped, so a check could still be running for a moment after its result came back.
    Returns the pids that had to be killed."""
    import signal
    import time
    root = os.path.realpath(root)

    def working():
        found = []
        for entry in os.scandir('/proc'):
            if entry.name.isdigit():
                try:
                    if os.readlink(f'/proc/{entry.name}/cwd') == root:
                        found.append(int(entry.name))
                except OSError:
                    continue
        return found

    killed = []
    for attempt in range(2):
        deadline = time.monotonic() + seconds
        while (left := working()) and time.monotonic() < deadline:
            time.sleep(0.02)
        if not left or attempt:
            return killed
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except OSError:
                pass
    return killed
