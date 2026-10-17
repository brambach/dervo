"""Checks on Windows: the stand-in for macOS sandbox-exec (b16/windows, PLAN-windows-m5-20260926.md, 27 Sep additions).

A check runs in an AppContainer with no capabilities, started from a restricted copy of the user's token, inside a job
object that allows exactly one process. Every access has to pass three checks: the user's own, the container's (only
"ALL APPLICATION PACKAGES" grants and the container's SID) and the restricted one (only Everyone, Users and CHECKS_SID).

- Reads: the snapshot folder and Dervo's Python, which Dervo grants the container and CHECKS_SID (grant_read), and
  system files both Users and app containers may read: Windows, Program Files and HKLM. Not the user's profile, temp
  folder, Documents or registry hive (HKCU grants app containers but not Users or Everyone), not network or Parallels
  shared drives.
- Writes: none, not even the container's own folder.
- Network: no capabilities, so no internet, private network or loopback.
- Child processes: the job's active-process limit is 1, so creating a second process fails.
- Environment: only what the host passes (HOME empty, no PATH; SystemRoot, without which Winsock can't start), plus
  what Windows sets for every AppContainer: LOCALAPPDATA, TEMP and TMP in the container's own folder, and
  PROCESSOR_ARCHITECTURE.

All of that was checked on Windows 11 26200 (Parallels) on 2 Oct 2026, and the host proves it again with its canary
before any project code runs (task_evidence.TaskEvidence._verify), as on a Mac. Any failure to set the container up is
Unavailable, which the host turns into ConfinementBlocked, never an unconfined retry.
"""
import ctypes
import os
import subprocess
import threading
import time

CONTAINER = 'Dervo.Checks'
CONFINEMENT = 'windows-appcontainer'
RESTRICTING = ('S-1-1-0', 'S-1-5-32-545')  # Everyone and Users, which system files need; CHECKS_SID is added
# A SID only Dervo grants, on the runtime and the check's snapshot (grant_read). A restricted token can't list the
# container's own SID or a capability, so the restricted half of every access check needs this one.
CHECKS_SID = 'S-1-5-21-1337713032-3551916707-1207002022-1001'

if os.name == 'nt':
    from ctypes import wintypes as wt
    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _adv = ctypes.WinDLL('advapi32', use_last_error=True)
    _env = ctypes.WinDLL('userenv', use_last_error=True)

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [('nLength', wt.DWORD), ('lpSecurityDescriptor', wt.LPVOID), ('bInheritHandle', wt.BOOL)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [('cb', wt.DWORD), ('lpReserved', wt.LPWSTR), ('lpDesktop', wt.LPWSTR), ('lpTitle', wt.LPWSTR),
                    ('dwX', wt.DWORD), ('dwY', wt.DWORD), ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD),
                    ('dwXCountChars', wt.DWORD), ('dwYCountChars', wt.DWORD), ('dwFillAttribute', wt.DWORD),
                    ('dwFlags', wt.DWORD), ('wShowWindow', wt.WORD), ('cbReserved2', wt.WORD),
                    ('lpReserved2', ctypes.c_void_p), ('hStdInput', wt.HANDLE), ('hStdOutput', wt.HANDLE),
                    ('hStdError', wt.HANDLE)]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [('StartupInfo', _STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', wt.HANDLE), ('hThread', wt.HANDLE), ('dwProcessId', wt.DWORD), ('dwThreadId', wt.DWORD)]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wt.DWORD)]

    class _TRUSTEE(ctypes.Structure):
        _fields_ = [('pMultipleTrustee', ctypes.c_void_p), ('MultipleTrusteeOperation', ctypes.c_int),
                    ('TrusteeForm', ctypes.c_int), ('TrusteeType', ctypes.c_int), ('ptstrName', ctypes.c_void_p)]

    class _EXPLICIT_ACCESS(ctypes.Structure):
        _fields_ = [('grfAccessPermissions', wt.DWORD), ('grfAccessMode', ctypes.c_int), ('grfInheritance', wt.DWORD),
                    ('Trustee', _TRUSTEE)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [('AppContainerSid', ctypes.c_void_p), ('Capabilities', ctypes.c_void_p),
                    ('CapabilityCount', wt.DWORD), ('Reserved', wt.DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                           'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', wt.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wt.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wt.DWORD), ('SchedulingClass', wt.DWORD)]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', _IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    for _name, _lib, _res, _args in (
            ('CreateAppContainerProfile', _env, ctypes.c_long, [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]),
            ('DeleteAppContainerProfile', _env, ctypes.c_long, [wt.LPCWSTR]),
            ('DeriveAppContainerSidFromAppContainerName', _env, ctypes.c_long, [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]),
            ('FreeSid', _adv, ctypes.c_void_p, [ctypes.c_void_p]),
            ('ConvertSidToStringSidW', _adv, wt.BOOL, [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]),
            ('ConvertStringSidToSidW', _adv, wt.BOOL, [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]),
            ('OpenProcessToken', _adv, wt.BOOL, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]),
            ('GetTokenInformation', _adv, wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]),
            ('SetTokenInformation', _adv, wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]),
            ('CreateRestrictedToken', _adv, wt.BOOL, [wt.HANDLE, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.HANDLE)]),
            ('GetNamedSecurityInfoW', _adv, wt.DWORD, [wt.LPCWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
            ('SetEntriesInAclW', _adv, wt.DWORD, [wt.ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
            ('SetNamedSecurityInfoW', _adv, wt.DWORD, [wt.LPWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                                        ctypes.c_void_p, ctypes.c_void_p]),
            ('CreateProcessAsUserW', _adv, wt.BOOL, [wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION)]),
            ('GetCurrentProcess', _k32, wt.HANDLE, []),
            ('LocalFree', _k32, ctypes.c_void_p, [ctypes.c_void_p]),
            ('CreatePipe', _k32, wt.BOOL, [ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), ctypes.POINTER(_SECURITY_ATTRIBUTES), wt.DWORD]),
            ('CreateFileW', _k32, wt.HANDLE, [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(_SECURITY_ATTRIBUTES), wt.DWORD, wt.DWORD, wt.HANDLE]),
            ('CloseHandle', _k32, wt.BOOL, [wt.HANDLE]),
            ('InitializeProcThreadAttributeList', _k32, wt.BOOL, [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]),
            ('UpdateProcThreadAttribute', _k32, wt.BOOL, [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]),
            ('DeleteProcThreadAttributeList', _k32, None, [ctypes.c_void_p]),
            ('CreateJobObjectW', _k32, wt.HANDLE, [ctypes.c_void_p, wt.LPCWSTR]),
            ('SetInformationJobObject', _k32, wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]),
            ('AssignProcessToJobObject', _k32, wt.BOOL, [wt.HANDLE, wt.HANDLE]),
            ('TerminateJobObject', _k32, wt.BOOL, [wt.HANDLE, wt.UINT]),
            ('TerminateProcess', _k32, wt.BOOL, [wt.HANDLE, wt.UINT]),
            ('ResumeThread', _k32, wt.DWORD, [wt.HANDLE]),
            ('WaitForSingleObject', _k32, wt.DWORD, [wt.HANDLE, wt.DWORD]),
            ('GetExitCodeProcess', _k32, wt.BOOL, [wt.HANDLE, ctypes.POINTER(wt.DWORD)])):
        getattr(_lib, _name).restype = _res
        getattr(_lib, _name).argtypes = _args

_ATTRIBUTE_HANDLE_LIST = 0x00020002
_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_READ_EXECUTE = 0x1200A9  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE
_GRANT_ACCESS = 1
_SE_FILE_OBJECT = 1
_DACL = 4  # DACL_SECURITY_INFORMATION
_TOKEN_USER = 1
_TOKEN_DEFAULT_DACL = 6
_CREATE_SUSPENDED = 0x4
_CREATE_UNICODE_ENVIRONMENT = 0x400
_CREATE_NO_WINDOW = 0x08000000
_EXTENDED_STARTUPINFO_PRESENT = 0x80000
_STARTF_USESTDHANDLES = 0x100
_JOB_LIMIT_ACTIVE_PROCESS = 0x8
_JOB_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_ALREADY_EXISTS = -2147024713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
_NOT_FOUND = -2147024894  # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
_WAIT_TIMEOUT = 0x102
_one_at_a_time = threading.Lock()


class Unavailable(Exception):
    """The container couldn't be set up. The caller turns this into ConfinementBlocked; nothing runs unconfined."""


def _fail(what):
    raise Unavailable(f'{what} failed: {ctypes.FormatError(ctypes.get_last_error())}')


def _sid(text, frees):
    found = ctypes.c_void_p()
    if not _adv.ConvertStringSidToSidW(text, ctypes.byref(found)):
        _fail(f'Reading SID {text}')
    frees.append(found.value)
    return found.value


def _sid_text(sid):
    text = wt.LPWSTR()
    if not _adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        _fail('ConvertSidToStringSid')
    try:
        return text.value
    finally:
        _k32.LocalFree(text)


def _token_information(token, kind):
    needed = wt.DWORD()
    _adv.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
    buffer = ctypes.create_string_buffer(needed.value)
    if not _adv.GetTokenInformation(token, kind, buffer, needed, ctypes.byref(needed)):
        _fail('Reading Dervo\'s token')
    return buffer


def container_sid():
    """The checks container's SID as a string, creating its profile if there isn't one (per user, no capabilities)."""
    sid = ctypes.c_void_p()
    result = _env.CreateAppContainerProfile(CONTAINER, 'Dervo checks', 'Runs Dervo automated checks with no access.', None, 0, ctypes.byref(sid))
    if result == _ALREADY_EXISTS:
        result = _env.DeriveAppContainerSidFromAppContainerName(CONTAINER, ctypes.byref(sid))
    if result != 0:
        raise Unavailable(f'AppContainer profile failed: HRESULT {result & 0xFFFFFFFF:#010x}')
    try:
        return _sid_text(sid)
    finally:
        _adv.FreeSid(sid)


def _user_sid():
    own = wt.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x8, ctypes.byref(own)):  # TOKEN_QUERY
        _fail('Opening Dervo\'s token')
    try:
        return _sid_text(ctypes.c_void_p.from_buffer(_token_information(own, _TOKEN_USER)).value)
    finally:
        _k32.CloseHandle(own)


def grant_read(path):
    """Read and execute on ``path`` (a folder's is inherited by everything in it) for the container and CHECKS_SID.
    Only ever Dervo's own folders: its Python and a check's snapshot. The ACL API rather than icacls, which refuses a SID
    no account has.

    The user is added too: Python 3.13's mkdtemp folders grant only OWNER RIGHTS, which doesn't let a restricted token
    in (checked 2 Oct 2026). The user owns those folders already, so nobody else gains anything."""
    frees = []
    try:
        sids = [_sid(text, frees) for text in (container_sid(), CHECKS_SID, _user_sid())]
        inherit = 3 if os.path.isdir(path) else 0  # SUB_CONTAINERS_AND_OBJECTS_INHERIT
        entries = (_EXPLICIT_ACCESS * len(sids))(*[_EXPLICIT_ACCESS(_READ_EXECUTE, _GRANT_ACCESS, inherit, _TRUSTEE(None, 0, 0, 0, s))
                                                   for s in sids])  # TRUSTEE_IS_SID
        old, descriptor, new = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        result = _adv.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL, None, None, ctypes.byref(old), None, ctypes.byref(descriptor))
        if result:
            raise Unavailable(f'Reading the permissions of {path} failed: {ctypes.FormatError(result)}')
        frees.append(descriptor.value)
        result = _adv.SetEntriesInAclW(len(sids), entries, old, ctypes.byref(new))
        if result:
            raise Unavailable(f'Adding the check to the permissions of {path} failed: {ctypes.FormatError(result)}')
        frees.append(new.value)
        result = _adv.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL, None, None, new, None)
        if result:
            raise Unavailable(f'Granting the check read access to {path} failed: {ctypes.FormatError(result)}')
    finally:
        for memory in frees:
            _k32.LocalFree(memory)


def _environment_block(env):
    # CreateProcess fails with ERROR_ENVVAR_NOT_FOUND for an AppContainer whose block has no LOCALAPPDATA, because it
    # rewrites that, TEMP and TMP to the container's own folder (Packages\dervo.checks\AC). So the user's path goes in
    # and only the container's comes out.
    env = {'LOCALAPPDATA': os.environ.get('LOCALAPPDATA', ''), **env}
    text = ''.join(f'{k}={v}\0' for k, v in sorted(env.items(), key=lambda item: item[0].upper())) + '\0'
    return ctypes.create_unicode_buffer(text, len(text))


def _restricted_token(frees):
    """A copy of this process's token with no privileges and RESTRICTING plus CHECKS_SID as its restricting SIDs."""
    own = wt.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x02000000, ctypes.byref(own)):  # MAXIMUM_ALLOWED
        _fail('Opening Dervo\'s token')
    try:
        sids = [_sid(text, frees) for text in (*RESTRICTING, CHECKS_SID)]
        listed = (_SID_AND_ATTRIBUTES * len(sids))(*[_SID_AND_ATTRIBUTES(s, 0) for s in sids])
        token = wt.HANDLE()
        if not _adv.CreateRestrictedToken(own, 0x1, 0, None, 0, None, len(sids), listed, ctypes.byref(token)):  # DISABLE_MAX_PRIVILEGE
            _fail('Making the restricted token')
        # What the check creates for itself (its token, threads, events) gets the token's default DACL, which names only
        # the user and SYSTEM. The restricted half of the check would then refuse the process its own objects and DLL
        # start-up fails (0xC0000142), so CHECKS_SID joins that default.
        current = _token_information(token, _TOKEN_DEFAULT_DACL)
        entry = _EXPLICIT_ACCESS(0x10000000, _GRANT_ACCESS, 0, _TRUSTEE(None, 0, 0, 0, sids[-1]))  # GENERIC_ALL
        new = ctypes.c_void_p()
        result = _adv.SetEntriesInAclW(1, ctypes.byref(entry), ctypes.c_void_p.from_buffer(current).value, ctypes.byref(new))
        if result:
            _k32.CloseHandle(token)
            raise Unavailable(f'Adding the check to its default permissions failed: {ctypes.FormatError(result)}')
        frees.append(new.value)
        default = ctypes.c_void_p(new.value)  # TOKEN_DEFAULT_DACL is one PACL
        if not _adv.SetTokenInformation(token, _TOKEN_DEFAULT_DACL, ctypes.byref(default), ctypes.sizeof(default)):
            _k32.CloseHandle(token)
            _fail('Setting the token\'s default permissions')
        return token
    finally:
        _k32.CloseHandle(own)


def _delete_profile():
    result = _env.DeleteAppContainerProfile(CONTAINER)
    if result not in (0, _NOT_FOUND):
        raise Unavailable(f'Clearing the checks container failed: HRESULT {result & 0xFFFFFFFF:#010x}')


def run(argv, cwd, env, *, timeout, output_limit, cancel=None):
    """Run ``argv`` confined in ``cwd`` (which the caller has passed to grant_read) and capture its output.

    Returns (exit_status, {'stdout': bytes, 'stderr': bytes}, reason), reason being None, 'timeout', 'cancelled' or
    'output_limit' as on a Mac. One run at a time, each with a new container profile that's deleted afterwards, so its
    folder starts empty whatever an earlier run did there. The SID comes from the name, so read grants survive."""
    with _one_at_a_time:
        _delete_profile()
        try:
            return _run(argv, cwd, env, timeout=timeout, output_limit=output_limit, cancel=cancel)
        finally:
            try:
                _delete_profile()
            except Unavailable:
                pass  # the next run refuses to start until its own delete works


def _run(argv, cwd, env, *, timeout, output_limit, cancel):
    container_sid()  # a new, empty profile
    sid = ctypes.c_void_p()
    if _env.DeriveAppContainerSidFromAppContainerName(CONTAINER, ctypes.byref(sid)) != 0:
        raise Unavailable('AppContainer SID lookup failed')
    handles, streams, frees, job, attributes, info = [], {}, [], None, None, _PROCESS_INFORMATION()
    try:
        inherit = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
        pipes = {}
        for name in ('stdout', 'stderr'):
            read, write = wt.HANDLE(), wt.HANDLE()
            if not _k32.CreatePipe(ctypes.byref(read), ctypes.byref(write), ctypes.byref(inherit), 0):
                _fail('CreatePipe')
            handles += [read, write]
            os.set_handle_inheritable(read.value, False)
            pipes[name] = (read, write)
        null = _k32.CreateFileW('NUL', 0x80000000, 0x3, ctypes.byref(inherit), 3, 0, None)
        if null in (None, wt.HANDLE(-1).value):
            _fail('Opening NUL')
        null = wt.HANDLE(null)
        handles.append(null)

        size = ctypes.c_size_t()
        _k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not _k32.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
            _fail('InitializeProcThreadAttributeList')
        capabilities = _SECURITY_CAPABILITIES(sid, None, 0, 0)
        if not _k32.UpdateProcThreadAttribute(attributes, 0, _ATTRIBUTE_SECURITY_CAPABILITIES, ctypes.byref(capabilities),
                                              ctypes.sizeof(capabilities), None, None):
            _fail('Setting the AppContainer')
        # Only these three handles reach the child, whatever else the host has open and inheritable.
        inherited = (wt.HANDLE * 3)(null, pipes['stdout'][1], pipes['stderr'][1])
        if not _k32.UpdateProcThreadAttribute(attributes, 0, _ATTRIBUTE_HANDLE_LIST, inherited, ctypes.sizeof(inherited), None, None):
            _fail('Setting the inherited handles')

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = null, pipes['stdout'][1], pipes['stderr'][1]
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)

        job = _k32.CreateJobObjectW(None, None)
        if not job:
            _fail('CreateJobObject')
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = _JOB_LIMIT_ACTIVE_PROCESS | _JOB_LIMIT_KILL_ON_JOB_CLOSE | _JOB_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        if not _k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)):
            _fail('Limiting the check to one process')

        token = _restricted_token(frees)
        handles.append(token)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW | _EXTENDED_STARTUPINFO_PRESENT
        # A restricted copy of the caller's own token needs no privilege to start a process with.
        if not _adv.CreateProcessAsUserW(token, argv[0], command, None, None, True, flags, ctypes.cast(_environment_block(env), ctypes.c_void_p),
                                         str(cwd), ctypes.byref(startup), ctypes.byref(info)):
            _fail('Starting the check')
        handles += [wt.HANDLE(info.hProcess), wt.HANDLE(info.hThread)]
        if not _k32.AssignProcessToJobObject(job, info.hProcess):
            _k32.TerminateProcess(info.hProcess, 1)
            _fail('Putting the check in its job')
        for name in ('stdout', 'stderr'):  # the child has its own copies now
            _k32.CloseHandle(pipes[name][1])
            handles.remove(pipes[name][1])
        _k32.CloseHandle(null)
        handles.remove(null)
        # The read ends become file objects here, before any reader starts, so only the finally below ever closes them.
        import msvcrt
        for name in ('stdout', 'stderr'):
            streams[name] = os.fdopen(msvcrt.open_osfhandle(pipes[name][0].value, os.O_RDONLY), 'rb', buffering=0)
            handles.remove(pipes[name][0])
        _k32.ResumeThread(info.hThread)

        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        over = threading.Event()
        lock = threading.Lock()

        def drain(name):
            while True:
                chunk = streams[name].read(4096)
                if not chunk:
                    return
                with lock:
                    remaining = output_limit - sum(map(len, buffers.values()))
                    buffers[name].extend(chunk[:max(0, remaining)])
                    if len(chunk) > remaining:
                        over.set()
        readers = [threading.Thread(target=drain, args=(name,), daemon=True) for name in buffers]
        for reader in readers:
            reader.start()
        reason, deadline = None, time.monotonic() + timeout
        while True:
            if _k32.WaitForSingleObject(info.hProcess, 50) != _WAIT_TIMEOUT:
                break
            if over.is_set():
                reason = 'output_limit'
            elif cancel is not None and cancel.is_set():
                reason = 'cancelled'
            elif time.monotonic() >= deadline:
                reason = 'timeout'
            if reason:
                _k32.TerminateJobObject(job, 1)
                _k32.WaitForSingleObject(info.hProcess, 5000)
                break
        for reader in readers:
            reader.join(5)
        if over.is_set() and not reason:
            reason = 'output_limit'
        code = wt.DWORD()
        _k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return ctypes.c_int32(code.value).value, {k: bytes(v) for k, v in buffers.items()}, reason
    finally:
        if job:
            _k32.TerminateJobObject(job, 1)
            _k32.CloseHandle(job)
        if attributes is not None:
            _k32.DeleteProcThreadAttributeList(attributes)
        for stream in streams.values():
            stream.close()
        for handle in handles:
            _k32.CloseHandle(handle)
        for memory in frees:
            _k32.LocalFree(memory)
        _adv.FreeSid(sid)
