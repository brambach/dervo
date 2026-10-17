"""Claude Code's version, and the oldest one Dervo's runs work with (PLAN-native.md, A7).

Dervo's flags need Claude Code 2.1.267 or later, and resuming a normal-mode session needs 2.1.271, which fixed resuming a
session whose only tools are MCP tools. Older versions fail partway with an unknown option, so Dervo checks before a
run starts, at first run, and on Connected accounts, and says what to run instead.

``claude --version`` prints "2.1.274 (Claude Code)". Reading it starts no session and makes no model call. The answer is
kept per executable file (its path, size and modification time), so a run doesn't pay for it twice, and an update to
the file is seen at once.

It runs on the request thread, before ``claude auth status``, and the desktop app ends the backend when one request
takes over 10 seconds (``src/bridge.rs``). So the read gets 2 seconds, and a failed read is kept for a minute: a hanging
binary costs the first run 2 seconds, not every run (security review, 19 Sep, finding 8).
"""
import re
import subprocess
import time

from dervo_connections import Rejected

FLOOR = (2, 1, 271)
FLOOR_TEXT = '.'.join(map(str, FLOOR))
UPDATE_COMMAND = 'claude update'
OLD_DETAIL = f'Update Claude Code to {FLOOR_TEXT} or later. Run {UPDATE_COMMAND} in Terminal, then try again.'
# Models that need a newer Claude Code than FLOOR. Opus 5.5 arrived in 2.1.280, when it became the default (Claude Code's
# model configuration page, checked 23 Sep 2026). Sonnet 5.5 needs 2.1.284 (the same page, checked 29 Sep 2026).
# frontend/src/local/models.ts CLAUDE_MODEL_FLOORS names the same numbers.
MODEL_FLOORS = {'claude-opus-5-5': (2, 1, 280), 'claude-sonnet-5-5': (2, 1, 284)}
MODEL_NAMES = {'claude-opus-5-5': 'Opus 5.5', 'claude-sonnet-5-5': 'Sonnet 5.5'}
FIELDS = {'claude_version': {'recheck'}}
TIMEOUT = 2  # seconds for --version, on the request thread
RETRY_AFTER = 60  # seconds a failed read is kept before trying again
_seen = {}
_failed = {}  # executable key -> when its read last failed (monotonic)


def parse(text):
    """(major, minor, patch) from the first version-looking word, or None."""
    if type(text) is not str:
        return None
    match = re.search(r'(?<![\d.])(\d{1,4})\.(\d{1,4})\.(\d{1,6})(?![\d.])', text[:400])
    return tuple(int(part) for part in match.groups()) if match else None


def too_old(version):
    return version is not None and tuple(version) < FLOOR


def _environment():
    from transport import account_home
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': account_home(), 'LANG': 'en_US.UTF-8'}


def _key(path):
    from pathlib import Path
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (str(path), stat.st_size, stat.st_mtime_ns)


def read(path, *, run=subprocess.run, fresh=False, clock=time.monotonic):
    """The version tuple of the Claude Code at ``path``, or None when it couldn't be read. ``fresh`` (Check again) skips
    both the kept answer and a kept failure."""
    key = _key(path)
    if key is None:
        return None
    if not fresh and key in _seen:
        return _seen[key]
    if not fresh and key in _failed and clock() - _failed[key] < RETRY_AFTER:
        return None
    try:
        result = run([str(path), '--version'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT, env=_environment())
        version = parse(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, Rejected):
        version = None
    if version is not None:
        _seen[key] = version
        _failed.pop(key, None)
    else:
        _failed[key] = clock()
    return version


def status(*, run=subprocess.run, fresh=False, find=None):
    """What Connected accounts shows under Claude: the installed version and whether runs can use it."""
    import claude_policy
    try:
        path = (find or claude_policy.executable)()
    except Rejected:
        return {'state': 'missing', 'version': None, 'floor': FLOOR_TEXT, 'command': None,
                'detail': 'Claude Code isn’t installed on this Mac.'}
    version = read(path, run=run, fresh=fresh)
    text = '.'.join(map(str, version)) if version else None
    if version is None:
        return {'state': 'unknown', 'version': None, 'floor': FLOOR_TEXT, 'command': None,
                'detail': 'Dervo couldn’t read which Claude Code version is installed.'}
    if too_old(version):
        return {'state': 'old', 'version': text, 'floor': FLOOR_TEXT, 'command': UPDATE_COMMAND,
                'detail': f'Dervo’s runs need {FLOOR_TEXT} or later, so no Claude account can run until it’s updated.'}
    return {'state': 'ok', 'version': text, 'floor': FLOOR_TEXT, 'command': None, 'detail': f'Claude Code {text} on this Mac'}


def _floor_key(model):
    """The MODEL_FLOORS entry ``model`` belongs to, including a dated snapshot or the 1M-context form someone typed
    (``claude-opus-5-5-20260922``, ``claude-opus-5-5[1m]``), or None."""
    if type(model) is not str:
        return None
    for key in MODEL_FLOORS:
        if re.fullmatch(re.escape(key) + r'(?:-\d{8})?(?:\[1m\])?', model):
            return key
    return None


def model_detail(model, version):
    """Why this Claude Code can't run ``model``, or None. Only a version that's known and below the model's floor says so."""
    key = _floor_key(model)
    if key is None or version is None or tuple(version) >= MODEL_FLOORS[key]:
        return None
    needed, have = '.'.join(map(str, MODEL_FLOORS[key])), '.'.join(map(str, version))
    return (f'{MODEL_NAMES[key]} needs Claude Code {needed} or later, and this Mac has {have}. '
            f'Run {UPDATE_COMMAND} in Terminal, then try again, or pick another model.')


def default_model(version):
    """The Claude model new work starts on: Opus 5.5 once this Claude Code can run it, otherwise Opus 5, which every
    version above FLOOR runs. An unknown version gets Opus 5. models.ts claudeDefaultModel follows the same rule."""
    if version is not None and model_detail('claude-opus-5-5', version) is None:
        return 'claude-opus-5-5'
    return 'claude-opus-5'


def require(path, *, run=subprocess.run, model=None):
    """Before a Claude run: refuse a Claude Code that's known to be too old, for Dervo or for the run's model. An
    unreadable version isn't a refusal; the run reports its own error if the flags turn out to be missing."""
    version = read(path, run=run)
    if too_old(version):
        raise Rejected(OLD_DETAIL)
    detail = model_detail(model, version)
    if detail:
        raise Rejected(detail)
