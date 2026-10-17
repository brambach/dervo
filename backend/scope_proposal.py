"""Draft a reviewable coding scope from a conversation message.

Provider independent and model free: the draft names only existing project files
that the message itself mentions, filtered by the same relative-path and sensitive
name rules the evidence broker enforces, plus the root unittest file the request
implies. It never reads file contents, never widens beyond mentioned files, and
never approves anything. A human still approves the scope before any tool runs.

Provider-drafted scope (asking Codex or Claude to propose files) is a separate
coordinator contract that isn't implemented yet; proposals carry
provider_proposal='unavailable' so the UI never implies it happened.
"""
from pathlib import Path
import re
from dervo_connections import Rejected
from task_evidence import TaskEvidence, FILE_LIMIT

SOURCE = 'heuristic'
TOKEN = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]{1,8}')
WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]{1,63}')
TEST_NAME = re.compile(r'test_[A-Za-z0-9_]+\.py')
LIMIT = 32


def acceptable(root, relative):
    """Existing regular file within the project, named by the same rules the broker enforces."""
    try:
        TaskEvidence._relative(relative)
    except Rejected:
        return False
    path = Path(root) / relative
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size <= FILE_LIMIT  # big files in scoped mode, beta.15
    except OSError:
        return False


def draft(cwd, text):
    root = Path(cwd)
    if type(text) is not str or not text.strip():
        raise Rejected('A development request needs text')
    files, mentions = [], []
    def add(relative):
        if relative not in files and acceptable(root, relative):
            files.append(relative)
    for token in TOKEN.findall(text):
        relative = token.strip('.,;:()[]"\'`')
        mentions.append(relative)
        add(relative)
    for word in WORD.findall(text):
        for relative in (f'{word}.py', f'test_{word}.py'):
            add(relative)
    for relative in list(files):
        stem = Path(relative).stem
        if not TEST_NAME.fullmatch(relative):
            add(f'test_{stem}.py')
    tests = [f for f in files if TEST_NAME.fullmatch(f)]
    if files and not tests:
        try:
            root_tests = sorted(p.name for p in root.iterdir() if TEST_NAME.fullmatch(p.name) and acceptable(root, p.name))
        except OSError:
            root_tests = []
        if len(root_tests) == 1:
            add(root_tests[0])
            tests = root_tests
    files = files[:LIMIT]
    if not files:
        state, reason = 'needs_input', 'The request does not name an existing project file. Name the file, or enter the scope manually.'
    elif not tests:
        state, reason = 'needs_input', 'No root unittest file (test_*.py) could be identified for this request. Name it, or enter the scope manually.'
    else:
        state, reason = 'proposed', None
    return {'files': sorted(files), 'test_file': tests[0] if tests else None, 'state': state, 'reason': reason,
            'source': SOURCE, 'provider_proposal': 'unavailable', 'mentions': mentions[:LIMIT]}
