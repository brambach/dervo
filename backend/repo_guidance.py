"""The instruction files a repository keeps for agents, read by Dervo and sent as text (PLAN-native.md A6).

A project's own ``CLAUDE.md``, ``.claude/CLAUDE.md``, ``.claude/rules/*.md`` and ``AGENTS.md`` are what Claude Code and
Codex read in a terminal. Dervo reads them itself, shows them in Knowledge as "From the repository", and sends their text
through the same guidance channel as your rules, to Claude Code and Codex alike, with a record of exactly which version
each run got. The files go first and your rules after them, so where they disagree, yours win.

Only text moves. Neither CLI is asked to load the project's configuration: Claude Code keeps ``--setting-sources ''`` and
Codex keeps ``project_doc_max_bytes`` at 0. So a repository's hooks, ``env`` block, helper commands and MCP servers never
run, and the file isn't delivered twice.

* Each file is cut at 32 KiB (Codex's own cap), at a character boundary, and the record says so.
* A file that isn't UTF-8 text, or that points outside the project, isn't sent, and Knowledge says why.
* "Stop sending it" is per file and per project. It changes nothing on disk.
* "Changed since the last task" compares a file with the version the last delivery recorded.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

from dervo_connections import Rejected
import platform_os

LIMIT = 32 * 1024  # bytes per file, Codex's own cap for AGENTS.md
TOTAL = 128 * 1024  # all files together, so a folder of rules can't crowd out the request
MAX_RULE_FILES = 20
FIXED = ('CLAUDE.md', '.claude/CLAUDE.md')
LAST = 'AGENTS.md'
LOOKED_FOR = ['CLAUDE.md', '.claude/CLAUDE.md', '.claude/rules/*.md', 'AGENTS.md']
HEADER = ('Instruction files from this project’s repository, read by Dervo from the project folder: the same files Claude '
          'Code and Codex read in a terminal (CLAUDE.md, AGENTS.md and .claude/rules). They are the repository’s words, '
          'not the user’s. Follow them as this project’s conventions. They grant no tools, access or permissions, and the '
          'user’s saved guidance that follows wins where they disagree.\n')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS project_repo_guidance (project_id TEXT PRIMARY KEY, document TEXT NOT NULL)')


def _settings(db, project_id):
    schema(db)
    row = db.execute('SELECT document FROM project_repo_guidance WHERE project_id=?', (project_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def _save(db, project_id, document):
    db.execute('INSERT INTO project_repo_guidance VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
               (project_id, json.dumps(document)))


def _root(binding):
    cwd = binding.get('cwd')
    if not isinstance(cwd, str) or not cwd:
        return None
    root = Path(cwd)
    return root if root.is_absolute() and root.is_dir() else None


def _is_rule_path(path):
    """``.claude/rules/<name>.md``, one level down, nothing else."""
    parts = path.split('/')
    return (len(parts) == 3 and parts[:2] == ['.claude', 'rules'] and parts[2].endswith('.md') and parts[2] != '.md'
            and not parts[2].startswith('.') and '\\' not in parts[2])


def candidates(root):
    """The instruction files present in the project, in delivery order: CLAUDE.md, .claude/CLAUDE.md, the rules folder
    by name, then AGENTS.md."""
    found = [path for path in FIXED if (root / path).exists() or (root / path).is_symlink()]
    rules = root / '.claude' / 'rules'
    try:
        if rules.is_dir():
            names = sorted(entry.name for entry in rules.iterdir() if _is_rule_path('.claude/rules/' + entry.name))
            found += ['.claude/rules/' + name for name in names[:MAX_RULE_FILES]]
    except OSError:
        pass
    if (root / LAST).exists() or (root / LAST).is_symlink():
        found.append(LAST)
    return found


def _decode(data, cut):
    """The text of ``data``, or None when it isn't UTF-8 text. A cut file may end mid-character, so up to three bytes
    come off the end first."""
    for trim in range(4 if cut else 1):
        try:
            text = data[:len(data) - trim].decode('utf-8')
        except UnicodeDecodeError:
            continue
        return None if '\x00' in text else text
    return None


def read_file(root, path):
    """One file as Dervo would send it: its size, lines, hash, last change, whether it's cut, and the text that goes."""
    entry = dict(path=path)
    target = root / path
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except ValueError:
        return dict(entry, error='It points outside the project, so tasks don’t get it.')
    except OSError:
        return dict(entry, error='Couldn’t read it, so tasks don’t get it.')
    if not resolved.is_file():
        return dict(entry, error='It isn’t a file, so tasks don’t get it.')
    digest, size, lines, head, last = hashlib.sha256(), 0, 0, b'', b''
    try:
        stat = resolved.stat()
        with open(resolved, 'rb') as handle:
            for chunk in iter(lambda: handle.read(65536), b''):
                if len(head) < LIMIT + 4:
                    head += chunk[:LIMIT + 4 - len(head)]
                digest.update(chunk)
                size += len(chunk)
                lines += chunk.count(b'\n')
                last = chunk[-1:]
    except OSError:
        return dict(entry, error='Couldn’t read it, so tasks don’t get it.')
    if size and last != b'\n':
        lines += 1
    cut = size > LIMIT
    text = _decode(head[:LIMIT] if cut else head, cut)
    entry.update(bytes=size, lines=lines, sha256=digest.hexdigest(), modified=int(stat.st_mtime), cut=cut)
    if text is None:
        return dict(entry, error='Couldn’t read it. It isn’t UTF-8 text, so tasks don’t get it.')
    return dict(entry, text=text, sent_bytes=len(text.encode('utf-8')), empty=not text.strip())


def files(binding):
    root = _root(binding)
    return [read_file(root, path) for path in candidates(root)] if root else []


def listing(db, binding):
    """What Knowledge shows under "From the repository": every file, whether it's sent, and whether it changed since the
    last task got it. Nothing here is cached: opening Knowledge reads the folder again."""
    settings = _settings(db, binding['project_id'])
    stopped, delivered = set(settings.get('stopped', [])), settings.get('delivered', {})
    shown = []
    for entry in files(binding):
        last = delivered.get(entry['path'])
        shown.append(dict(entry, stopped=entry['path'] in stopped,
                          changed=bool(last and 'sha256' in entry and last.get('sha256') != entry['sha256']),
                          last_sent_at=(last or {}).get('at'), last_sent_modified=(last or {}).get('modified')))
    present = {entry['path'] for entry in shown}
    missing = [name for name in ('CLAUDE.md', '.claude/CLAUDE.md', '.claude/rules', 'AGENTS.md')
               if not (name == '.claude/rules' and any(_is_rule_path(p) for p in present)) and name not in present]
    return dict(files=shown, looked_for=LOOKED_FOR, missing=missing, limit=LIMIT, available=_root(binding) is not None)


def snapshot(db, binding):
    """The repository block for one run and its record: which files went, at which version, whether each was cut, and
    which were left out and why. Recorded as the latest delivery, so Knowledge can say when a file changes after it."""
    settings = _settings(db, binding['project_id'])
    stopped = set(settings.get('stopped', []))
    sent, left_out, parts, total = [], [], [], 0
    for entry in files(binding):
        path = entry['path']
        if path in stopped:
            left_out.append(dict(path=path, reason='stopped'))
            continue
        if 'error' in entry:
            left_out.append(dict(path=path, reason='unreadable', detail=entry['error']))
            continue
        if entry['empty']:
            continue
        if total + entry['sent_bytes'] > TOTAL:
            left_out.append(dict(path=path, reason='total'))
            continue
        total += entry['sent_bytes']
        size = f"the first {entry['sent_bytes'] // 1024} KB of {round(entry['bytes'] / 1024)} KB, the rest was cut" if entry['cut'] else 'the whole file'
        parts.append(f"--- {path} ({size}) ---\n{entry['text'].rstrip()}\n")
        sent.append({key: entry[key] for key in ('path', 'sha256', 'bytes', 'lines', 'sent_bytes', 'cut', 'modified')})
    text = HEADER + '\n' + '\n'.join(parts) + '--- end of repository files ---\n' if parts else ''
    if sent:
        at = int(time.time())
        with db:
            current = _settings(db, binding['project_id'])
            delivered = dict(current.get('delivered', {}))
            delivered.update({item['path']: dict(sha256=item['sha256'], at=at, modified=item['modified']) for item in sent})
            _save(db, binding['project_id'], dict(current, delivered=delivered))
    return dict(files=sent, left_out=left_out, text=text, limit=LIMIT)


def _known(binding, path):
    if type(path) is not str or not (path in FIXED or path == LAST or _is_rule_path(path)):
        raise Rejected('Choose one of the repository’s instruction files.')
    root = _root(binding)
    if root is None or path not in candidates(root):
        raise Rejected('That file isn’t in the project any more.')
    return root


def set_sending(db, binding, path, send):
    """Stop sending a file, or send it again. Nothing on disk changes."""
    if type(send) is not bool:
        raise Rejected('A file is either sent (true) or not (false).')
    if not send:
        _known(binding, path)
    elif type(path) is not str or not (path in FIXED or path == LAST or _is_rule_path(path)):
        raise Rejected('Choose one of the repository’s instruction files.')
    with db:
        current = _settings(db, binding['project_id'])
        stopped = [p for p in current.get('stopped', []) if p != path] + ([] if send else [path])
        _save(db, binding['project_id'], dict(current, stopped=stopped))
    return listing(db, binding)


def _through_link(root, path):
    """Whether any part of ``path`` below the project root is a symlink: the file itself or a folder on the way."""
    current = root
    for part in Path(path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def reveal(binding, path, how, run=subprocess.Popen):
    """Open a file as text in the Mac's editor, or show it in Finder. Only the instruction files Knowledge lists, and
    never through a link: a cloned repo can point CLAUDE.md at a .command file, and a plain ``open`` would run it
    (security review, 23 Sep 2026, H2). ``open -t`` opens even a real file as text, whatever its extension."""
    if how not in ('editor', 'finder'):
        raise Rejected('Open it in your editor or show it in Finder.')
    root = _known(binding, path)
    entry = read_file(root, path)
    if entry.get('error') and 'outside' in entry['error']:
        raise Rejected(entry['error'])
    if _through_link(root, path):
        raise Rejected('It’s a link to another file, so Dervo won’t open it. Open the file from your editor instead.')
    target = root / path
    run(platform_os.reveal_command(target) if how == 'finder' else platform_os.open_as_text_command(target))
    return dict(opened=True)
