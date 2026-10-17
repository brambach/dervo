"""Read-only project questions through the existing durable task and provider brokers."""
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager

from dervo_connections import Rejected
import platform_os
from task_evidence import TaskEvidence
from project_access import same_root

TOOLS = ('dervo_read_file', 'dervo_list_files', 'dervo_search_files')
DETAIL = ('Dervo can list, search and read regular files in the selected project. '
          'Hidden files, credential paths, links and generated dependency folders are excluded. '
          'This mode cannot edit files or run commands.')


def root_identity(coding, binding):
    # Reading is recursive, so also reject a project containing a host/profile store.
    coding.protect(binding['cwd'], [], ['.'])
    with TaskEvidence._directory(binding['cwd'], [], granted=None) as fd:
        info = os.fstat(fd)
        return [info.st_dev, info.st_ino]


def availability(coding, binding):
    if binding['provider'] not in ('codex', 'claude'):
        return dict(state='unavailable', detail='Project reading needs Codex or Claude Code.')
    try:
        from connections_ui import owned_profile
        owned_profile(coding.store, coding.host.root, binding['profile_id'], context=binding.get('context'))
        root_identity(coding, binding)
    except (Rejected, OSError) as exc:
        return dict(state='unavailable', detail=str(exc))
    return dict(state='available', detail=DETAIL)


def create(coding, binding, ctx, text):
    identity = root_identity(coding, binding)
    evidence = dict(id=str(uuid.uuid4()), **ctx, profile_id=binding['profile_id'], model=binding['model'],
                    root=binding['cwd'], visibility=binding['visibility'], read_only=True, root_identity=identity,
                    files=[], folders=[], created=[], admitted=[], scope_version=1, approval=None,
                    review=None, attempts=[], argv=None, check_profile=None, check_timeout_seconds=5)
    coding.evidence._save(evidence)
    return coding.save(dict(id=evidence['id'], context=ctx, visibility=binding['visibility'],
        state='needs_permission', session_id=None, contract='dervo-dynamic-tools-v1', read_only=True,
        root_identity=identity, binding={k:v for k,v in binding.items() if k != 'home'}, scope_version=1,
        files=[], folders=[], argv=None, check_profile=None, check_timeout_seconds=5,
        messages=[dict(role='user', text=text, created_at=int(time.time()))], tokens=[], runs=[], error=None))


class ProjectReader(TaskEvidence):
    """Reuse bounded enumeration/search and audit snapshots without a writable folder grant.

    A reader is constructed for one recorded task. Each directory walk checks the
    selected root's device/inode before opening descendants. No global reader state.
    """
    def __init__(self, coding, task):
        self.store = coding.store
        self.task_id = task['id']
        self.identity = task['root_identity']
        binding = self.store.binding(**task['context'])
        if not same_root(self.identity, root_identity(coding, binding)):
            raise Rejected('The project folder was replaced. Start a new project question.')

    def read(self, task_id, **context):
        task = super().read(task_id, **context)
        if task_id != self.task_id or task.get('read_only') is not True or task.get('root_identity') != self.identity:
            raise Rejected('Project reading scope changed')
        return task

    @staticmethod
    def _relative(path):
        if path == '.':
            return []  # Directory tools alone accept the selected root.
        parts = TaskEvidence._relative(path)
        if any(p.lower() in ('node_modules', 'target', 'dist', 'build', '__pycache__', 'venv')
               or p.lower().endswith(('.pem', '.key', '.p12', '.pfx', '.sqlite', '.db'))
               or re.split(r'[._-]', p.lower())[0] in ('secret', 'secrets', 'token', 'tokens') for p in parts):
            raise Rejected('Generated dependencies or credential files are excluded from project reading')
        return parts

    @classmethod
    def _admits(cls, task, path):
        return bool(cls._relative(path)) and task.get('read_only') is True

    @contextmanager
    def _directory(self, root, parts, *, granted):
        with TaskEvidence._directory(root, [], granted=None) as root_fd:
            info = os.fstat(root_fd)
            if [info.st_dev, info.st_ino] != self.identity:
                raise Rejected('The project folder was replaced. Start a new project question.')
            descriptors = []
            fd = root_fd
            try:
                for part in parts:
                    fd = platform_os.open_dir_at(part, fd)
                    descriptors.append(fd)
                yield fd
            except OSError as exc:
                raise Rejected('Safe project reading failed: ' + str(exc)) from exc
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)

    @contextmanager
    def _file(self, task, path, writable=False):
        parts = self._relative(path)
        if writable or not parts:
            raise Rejected('Project reading only opens regular files for reading')
        with self._directory(task['root'], parts[:-1], granted=None) as directory:
            try:
                fd = platform_os.open_file_at(parts[-1], directory)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
                        raise Rejected('File must be a single-link regular file up to 256 KiB')
                    yield fd
                finally:
                    os.close(fd)
            except OSError as exc:
                raise Rejected('Safe project reading failed: ' + str(exc)) from exc
