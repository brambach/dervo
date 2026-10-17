"""The whole-Mac look for first run (beta.15 group 10, evidence/design-tests/onboarding-v2.md): which folders you've
worked in with Claude Code and Codex, the noise set aside by rule, and the projects worth suggesting. Metadata only.

What's read, and only read:

- ``~/.claude/projects``: the folder list and, inside each folder, the names, sizes and dates of the top-level
  ``<uuid>.jsonl`` files. No transcript is opened, not even its first line. A folder's name is Claude Code's slug of
  the path it ran in (every character but letters and digits turned into a hyphen), so the real path is found by
  matching the slug against directory names on disk (``Resolver``), never by reading the ``cwd`` inside a transcript.
- ``~/.codex/state_5.sqlite``, opened read-only: the ``threads`` columns ``id``, ``cwd``, ``source``, ``created_at``,
  ``updated_at``, ``archived``, ``git_origin_url`` and ``git_branch``, and the folder column of ``project_roots``.
  Never ``first_user_message``, ``title``, ``name`` or ``preview``.
- ``~/.codex/external_agent_session_imports.json``: which Codex threads are Codex's copies of Claude Code sessions
  (thread ids and file names only, through ``history_import.codex_imports``).
- A kept folder's ``.git`` entry, to fold worktrees and subfolders into their repository: whether it's a directory,
  and for a worktree the one ``gitdir:`` line git writes there.

Nothing is written anywhere, no model is called and nothing leaves the Mac. The rules are about shapes (temporary,
run by a tool, one-off, a copy), not about Dervo's own paths, so they hold on a Mac without Dervo's test harness.
"""
import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from history_import import CLAUDE_HOME, CODEX_HOME, CODEX_OWN_SOURCES, CODEX_STATE, UUID, _when, codex_imports
from session_continuity import _slug as slug_of

RECENT_DAYS = 14  # "active": something in the last two weeks
TICK_CONVERSATIONS = 3  # and at least this many conversations, for a suggestion to come pre-ticked
TEMP_ROOTS = ('/private/tmp', '/tmp', '/private/var/folders', '/var/folders')
WALK_DEPTH = 16  # path components a slug may resolve through
WALK_LISTINGS = 4000  # directory listings one survey may make while resolving slugs
LISTED = 200  # folders listed under each set-aside pile
WORK_WORDS = frozenset({'work', 'clients', 'client', 'company', 'employer', 'job', 'jobs'})
PERSONAL_WORDS = frozenset({'personal', 'side-projects', 'sideprojects', 'hobby', 'hobbies', 'playground'})
PUBLIC_HOSTS = frozenset({'github.com', 'gitlab.com', 'bitbucket.org', 'codeberg.org', 'sr.ht', 'git.sr.ht'})
CODEX_COLUMNS = ('id', 'cwd', 'source', 'created_at', 'updated_at', 'archived', 'git_origin_url', 'git_branch')
ROOT_COLUMNS = ('path', 'root_path', 'root', 'cwd', 'directory', 'folder')

# The piles, in the order the rules run and the screens list them. (key, one, many)
RULES = [
    ('temp', 'temporary folder', 'temporary folders'),
    ('gone', 'folder that’s gone', 'folders that are gone'),
    # Behaviour review S3 (24 Sep): a folder macOS or its permissions kept Dervo out of isn't gone. Lost observation is
    # said as such.
    ('unreadable', 'folder Dervo couldn’t look inside', 'folders Dervo couldn’t look inside'),
    ('tool_folder', 'tool’s own folder', 'tools’ own folders'),
    ('tool_run', 'thread a tool ran', 'threads a tool ran'),
    ('copy', 'Codex copy of a Claude Code session, counted once', 'Codex copies of Claude Code sessions, counted once'),
    ('folded', 'worktree or subfolder, folded into its repo', 'worktrees and subfolders, folded into their repo'),
    ('dervo', 'folder already in Dervo', 'folders already in Dervo'),
    ('one_off', 'one-off', 'one-offs'),
]
FOLDER_RULES = frozenset({'temp', 'gone', 'unreadable', 'tool_folder', 'folded', 'dervo', 'one_off'})
RESTORABLE = frozenset({'tool_folder', 'folded', 'one_off'})  # a folder you can bring back from its pile


def key_for(path):
    """A stable, opaque key for a folder. The screens hand keys back, never paths."""
    return hashlib.sha256(str(path).encode('utf-8', 'surrogatepass')).hexdigest()[:20]


def tilde(path, home):
    path, home = str(path), str(home)
    return '~' + path[len(home):] if path == home or path.startswith(home.rstrip('/') + '/') else path


# ---------------------------------------------------------------- slug to path, from directory names
class Resolver:
    """The folder a Claude Code slug came from, matched against directory names on disk, longest name first. The
    slug is lossy (``dd-app``, ``dd.app`` and ``dd/app`` all read ``dd-app``), so a path Codex or Dervo already knows
    wins, and otherwise the walk takes the first real directory whose every component matches. Only directories on
    the slug's own route are listed. None when nothing on disk matches: the folder is gone."""

    def __init__(self, known=(), listings=WALK_LISTINGS):
        self.known = {}
        for path in known:
            if isinstance(path, str) and path:
                self.known.setdefault(slug_of(path), path)
        self.listings, self.cache = listings, {}
        self.denied = set()  # folders Dervo couldn't list (macOS privacy protection, permissions)
        self.hit = False     # whether the last resolve's route ran into one of them

    def children(self, folder):
        if folder in self.cache:
            return self.cache[folder]
        found = []
        if self.listings > 0:
            self.listings -= 1
            try:
                with os.scandir(folder) as entries:
                    found = [(entry.name, '-' + slug_of(entry.name)) for entry in entries
                             if entry.is_dir(follow_symlinks=False)]
            except OSError:
                found = []
                self.denied.add(folder)
            found.sort(key=lambda item: -len(item[1]))
        self.cache[folder] = found
        return found

    def resolve(self, slug):
        self.hit = False
        known = self.known.get(slug)
        if known and os.path.isdir(known):
            return known
        return self._walk('/', slug, 0)

    def _walk(self, folder, rest, depth):
        if depth > WALK_DEPTH:
            return None
        children = self.children(folder)
        if folder in self.denied:
            self.hit = True
        for name, part in children:
            if not rest.startswith(part) or (len(rest) > len(part) and rest[len(part)] != '-'):
                continue
            path = os.path.join(folder, name)
            if len(rest) == len(part):
                return path
            found = self._walk(path, rest[len(part):], depth + 1)
            if found:
                return found
        return None


# ---------------------------------------------------------------- what each tool has
def claude_folders(home=CLAUDE_HOME, errors=None):
    """{slug: [(first, last), ...]} from file dates alone, one pair per top-level session file. A list that's there but
    can't be read adds 'claude' to ``errors`` (behaviour review S4), so it isn't shown as empty."""
    root = Path(home) / 'projects'
    found = {}
    try:
        folders = list(os.scandir(root))
    except OSError:
        if errors is not None and os.path.lexists(root):
            errors.add('claude')
        return found
    for folder in folders:
        if not folder.is_dir(follow_symlinks=False):
            continue
        stamps = []
        try:
            with os.scandir(folder.path) as entries:
                for entry in entries:
                    if not (entry.name.endswith('.jsonl') and UUID.fullmatch(entry.name[:-6])
                            and entry.is_file(follow_symlinks=False)):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    first = getattr(stat, 'st_birthtime', None) or stat.st_mtime
                    stamps.append((min(first, stat.st_mtime), stat.st_mtime))
        except OSError:
            if errors is not None:
                errors.add('claude')
            continue
        if stamps:
            found[folder.name] = stamps
    return found


def codex_state(home=CODEX_HOME, errors=None):
    """(threads, roots): the allowed ``threads`` columns as dicts, and the folders of Codex's own projects. Columns a
    Codex version doesn't have read as None. Empty when there's no database; one that's there but can't be opened or
    read adds 'codex' to ``errors`` (behaviour review S4)."""
    state = Path(home) / CODEX_STATE
    if not state.is_file():
        return [], []
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
    except sqlite3.Error:
        if errors is not None:
            errors.add('codex')
        return [], []
    threads, roots = [], []
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'threads' in tables:
            columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
            chosen = [name if name in columns else f'NULL AS {name}' for name in CODEX_COLUMNS]
            for row in db.execute(f'SELECT {", ".join(chosen)} FROM threads'):
                threads.append(dict(zip(CODEX_COLUMNS, row)))
        if 'project_roots' in tables:
            columns = [row[1] for row in db.execute('PRAGMA table_info(project_roots)')]
            column = next((name for name in ROOT_COLUMNS if name in columns), None)
            if column:
                roots = [row[0] for row in db.execute(f'SELECT {column} FROM project_roots') if isinstance(row[0], str) and row[0]]
    except sqlite3.Error:
        if errors is not None:
            errors.add('codex')
    finally:
        db.close()
    return threads, roots


# ---------------------------------------------------------------- rules
def is_temp(path, roots):
    return any(path == root or path.startswith(root.rstrip('/') + '/') for root in roots)


def tool_folder(path, home):
    """Under a dot folder in the home folder: ``~/.claude/...``, ``~/.cache/...``."""
    home = str(home).rstrip('/')
    if not path.startswith(home + '/'):
        return False
    return path[len(home) + 1:].split('/', 1)[0].startswith('.')


def repository(path, stop):
    """(repo root, how) for a folder inside a git repository: its own root, a worktree's main checkout, or the
    enclosing repository of a subfolder. Reads only whether ``.git`` is a directory and a worktree's one gitdir line."""
    current = Path(path)
    stop = Path(stop)
    for folder in [current, *current.parents]:
        if folder == folder.parent or (folder != current and folder in (stop, stop.parent)):
            break
        dot = folder / '.git'
        try:
            if dot.is_dir() and not dot.is_symlink():
                return str(folder), 'root' if folder == current else 'subfolder'
            if dot.is_file() and not dot.is_symlink():
                with open(dot, encoding='utf-8', errors='replace') as handle:
                    line = handle.read(1024).strip()
                match = re.match(r'gitdir:\s*(.+?)/\.git/worktrees/[^/]+/?$', line)
                if match and os.path.isdir(match.group(1)):
                    return os.path.realpath(match.group(1)), 'worktree'
                return str(folder), 'root' if folder == current else 'subfolder'
        except OSError:
            continue
    return None, None


def context_guess(path, origins, home):
    """('work', 'personal' or None, why), from the path and git remotes only. A guess, and the screens say so. None
    means nothing clear either way: the project goes under "Work or personal?" and waits for the person to pick,
    because a public remote or a plain ~/code folder says nothing about whose work it is (Bryce, 24 Sep 2026)."""
    relative = tilde(path, home).split('/')
    for part in relative:
        if part.casefold() in WORK_WORDS:
            index = relative.index(part)
            return 'work', f'guessed from {"/".join(relative[:index + 1])}'
    for origin in origins:
        host = remote_host(origin)
        if host and host not in PUBLIC_HOSTS:
            return 'work', f'guessed from its remote, {host}'
    for part in relative:
        if part.casefold() in PERSONAL_WORDS:
            index = relative.index(part)
            return 'personal', f'guessed from {"/".join(relative[:index + 1])}'
    return None, 'nothing here says work or personal'


def remote_host(origin):
    if not isinstance(origin, str) or not origin:
        return None
    match = re.match(r'^[\w.-]+@([\w.-]+):', origin)  # git@host:org/repo.git
    if match:
        return match.group(1).casefold()
    try:
        return (urlparse(origin).hostname or '').casefold() or None
    except ValueError:
        return None


def plural(count, one, many):
    return f'{count:,} {one if count == 1 else many}'


def ago(days):
    days = int(days)
    return 'today' if days <= 0 else 'yesterday' if days == 1 else f'{days} days ago'


def reason_for(place, now):
    """One checkable line from metadata: how many, how recently, which tools, which branch."""
    total, recent = place['conversations'], place['recent']
    parts = [plural(total, 'conversation', 'conversations')]
    if recent and recent != total:
        parts[0] += f', {recent} in the last {RECENT_DAYS} days'
    elif recent:
        parts[0] += f' in the last {RECENT_DAYS} days'
    else:
        parts.append(f'last one {ago((now - place["last"]) / 86400)}' if place['last'] else 'no dates')
    tools = place['tools']
    parts.append('in both Claude Code and Codex' if len(tools) == 2 else 'in Claude Code' if 'claude' in tools else 'in Codex')
    if place['branch']:
        parts.append(f'on branch {place["branch"]}')
    line = ', '.join(parts)
    if place['codex_project']:
        line += '. You made it a Codex project'
    return line[0].upper() + line[1:] + '.'


def _scratch():
    """Dervo's own runs' folder (platform_os.scratch_root), so a run's folder is never offered as a project."""
    try:
        import platform_os
        return (platform_os.scratch_root(),)
    except OSError:
        return ()


# ---------------------------------------------------------------- the survey
def survey(*, claude_home=CLAUDE_HOME, codex_home=CODEX_HOME, home=None, dervo_projects=(), temp_roots=None, now=None):
    """The look, in one pass. Returns the proposal, the piles and the counts, plus ``paths``: {key: real path} for
    the host to keep. The screens get everything but ``paths``."""
    started = time.monotonic()
    now = time.time() if now is None else now
    home = os.path.realpath(str(home or Path.home()))
    roots = tuple(temp_roots) if temp_roots is not None else TEMP_ROOTS + ((os.path.realpath(os.environ['TMPDIR']),) if os.environ.get('TMPDIR') else ()) + _scratch()
    recent_after = now - RECENT_DAYS * 86400
    piles = {rule: dict(count=0, folders={}) for rule, _, _ in RULES}

    def aside(rule, path, conversations):
        pile = piles[rule]
        pile['count'] += 1 if rule in FOLDER_RULES else conversations
        if path:
            pile['folders'][path] = pile['folders'].get(path, 0) + conversations

    places = {}

    def place(path):
        return places.setdefault(path, dict(path=path, stamps=[], tools=set(), branches={}, origins=set(), codex_project=False))

    # Codex first: its cwds are exact, and they help resolve Claude Code's slugs.
    errors = set()  # the tools whose list is there but couldn't be read
    threads, codex_roots = codex_state(codex_home, errors)
    copies = set(codex_imports(codex_home))
    files = len(threads)
    codex_folders = set()
    for thread in threads:
        cwd, source = thread.get('cwd'), thread.get('source')
        folder = os.path.realpath(cwd) if isinstance(cwd, str) and cwd and os.path.isdir(cwd) else cwd if isinstance(cwd, str) and cwd else None
        if folder:
            codex_folders.add(folder)
        if not isinstance(source, str) or source.startswith('{') or source not in CODEX_OWN_SOURCES:
            aside('tool_run', folder, 1)
            continue
        if thread.get('id') in copies:
            aside('copy', folder, 1)
            continue
        if not folder:
            continue
        first, last = _when(thread.get('created_at')), _when(thread.get('updated_at'))
        entry = place(folder)
        entry['stamps'].append((first or last or 0, last or first or 0))
        entry['tools'].add('codex')
        if isinstance(thread.get('git_branch'), str) and thread['git_branch']:
            entry['branches'][thread['git_branch']] = max(entry['branches'].get(thread['git_branch'], 0), last or 0)
        if isinstance(thread.get('git_origin_url'), str) and thread['git_origin_url']:
            entry['origins'].add(thread['git_origin_url'])
    root_paths = {os.path.realpath(path) for path in codex_roots if os.path.isdir(path)}
    for path in root_paths:
        place(path)['codex_project'] = True

    folders = set(codex_folders)
    resolver = Resolver([*codex_folders, *root_paths, *dervo_projects])
    temp_slugs = tuple(slug_of(root) + '-' for root in roots)
    home_dots = slug_of(home) + '--'
    for slug, stamps in claude_folders(claude_home, errors).items():
        files += len(stamps)
        if slug.startswith(temp_slugs) or slug in {slug_of(root) for root in roots}:
            folders.add('claude:' + slug)
            aside('temp', 'claude:' + slug, len(stamps))
            continue
        if slug.startswith(home_dots):
            folders.add('claude:' + slug)
            aside('tool_folder', 'claude:' + slug, len(stamps))
            continue
        path = resolver.resolve(slug)
        if not path:
            folders.add('claude:' + slug)
            aside('unreadable' if resolver.hit else 'gone', 'claude:' + slug, len(stamps))
            continue
        path = os.path.realpath(path)
        folders.add(path)
        entry = place(path)
        entry['stamps'].extend(stamps)
        entry['tools'].add('claude')

    dervo = {os.path.realpath(path) for path in dervo_projects if isinstance(path, str)}
    loose = None
    kept = {}
    for path, entry in places.items():
        count = len(entry['stamps'])
        if is_temp(path, roots):
            aside('temp', path, count)
        elif not os.path.isdir(path):
            aside('gone', path, count)
        elif tool_folder(path, home):
            aside('tool_folder', path, count)
        elif path in dervo:
            aside('dervo', path, count)
        elif path == home:
            loose = entry
        elif count or entry['codex_project']:
            kept[path] = entry

    # Worktrees and subfolders fold into their repository, which comes in as one project (made here if only its
    # worktrees had history). A subfolder of another kept folder folds into it the same way.
    merged = {}
    for path in sorted(kept, key=len):
        entry = kept[path]
        repo, how = repository(path, home)
        target = repo if repo and repo != path else next((other for other in merged if path.startswith(other.rstrip('/') + '/')), None)
        if target and target != path and not is_temp(target, roots) and target != home and target not in dervo:
            into = merged.get(target) or kept.get(target) or dict(path=target, stamps=[], tools=set(), branches={}, origins=set(), codex_project=False)
            merged[target] = into
            if into is not entry:
                into['stamps'].extend(entry['stamps'])
                into['tools'] |= entry['tools']
                into['origins'] |= entry['origins']
                into['codex_project'] = into['codex_project'] or entry['codex_project']
                for branch, at in entry['branches'].items():
                    into['branches'][branch] = max(into['branches'].get(branch, 0), at)
                aside('folded', path, len(entry['stamps']))
            continue
        if target in dervo:
            aside('dervo', path, len(entry['stamps']))
            continue
        merged.setdefault(path, entry)

    def facts(entry):
        stamps = entry['stamps']
        last = max((b for _, b in stamps), default=0) or None
        first = min((a for a, _ in stamps if a), default=None)
        branch = max(entry['branches'], key=entry['branches'].get) if entry['branches'] else None
        return dict(conversations=len(stamps), recent=sum(1 for _, b in stamps if b >= recent_after), last=last, first=first,
                    tools=sorted(entry['tools']), branch=branch, codex_project=entry['codex_project'])

    projects, paths = [], {}
    for path, entry in merged.items():
        summary = facts(entry)
        if summary['conversations'] <= 1 and not summary['recent'] and not summary['codex_project']:
            aside('one_off', path, summary['conversations'])
            continue
        context, why = context_guess(path, entry['origins'], home)
        key = key_for(path)
        paths[key] = path
        ticked = (summary['recent'] > 0 and summary['conversations'] >= TICK_CONVERSATIONS) or summary['codex_project']
        projects.append(dict(key=key, path=tilde(path, home), name=Path(path).name, context=context, context_reason=why,
                             reason=reason_for(summary, now), ticked=bool(ticked), **summary))
    projects.sort(key=lambda p: (not p['ticked'], -(p['last'] or 0), -p['conversations'], p['name'].casefold()))

    # Loose ends are said, never offered: the home folder can't come in as a project (Bryce, 24 Sep, on security review
    # N1 and behaviour review N2), so it has no key and isn't in ``paths``.
    loose_ends = None
    if loose and loose['stamps']:
        summary = facts(loose)
        loose_ends = dict(key=None, path='~', name='Loose ends', context=None, context_reason=None, ticked=False,
                          reason=f'{plural(summary["conversations"], "conversation", "conversations")} started in your home folder, '
                                 'not in a project. They stay where they are.', **summary)

    def shown(slug):
        """A set-aside Claude Code folder as you'd recognise it: its real path when it's still on disk, else the slug
        with your home folder written as ``~``. Only for listing; the rules above never needed the path."""
        found = resolver.resolve(slug)
        if found:
            return tilde(os.path.realpath(found), home)
        home_slug = slug_of(home)
        return '~/' + slug[len(home_slug) + 1:] if slug.startswith(home_slug + '-') else slug

    set_aside = []
    for rule, one, many in RULES:
        pile = piles[rule]
        if not pile['count']:
            continue
        listed = []
        for path, conversations in sorted(pile['folders'].items(), key=lambda item: -item[1])[:LISTED]:
            real = not path.startswith('claude:')
            item = dict(path=tilde(path, home) if real else shown(path[len('claude:'):]), conversations=conversations, key=None)
            if real and rule in RESTORABLE and os.path.isdir(path):
                item['key'] = key_for(path)
                item['name'] = Path(path).name
                paths.setdefault(item['key'], path)
            listed.append(item)
        set_aside.append(dict(rule=rule, count=pile['count'], label=plural(pile['count'], one, many), folders=listed,
                              more=max(0, len(pile['folders']) - LISTED)))

    ticked = sum(1 for p in projects if p['ticked'])
    counts = dict(files=files, folders=len(folders | set(places)), working=ticked, quieter=len(projects) - ticked,
                  loose=len(loose['stamps']) if loose else 0)
    sources = [dict(tool='claude', path=tilde(Path(claude_home) / 'projects', home), present=(Path(claude_home) / 'projects').is_dir()),
               dict(tool='codex', path=tilde(Path(codex_home), home), present=(Path(codex_home) / CODEX_STATE).is_file())]
    for source in sources:
        source['readable'] = source['tool'] not in errors
    return dict(sources=sources, counts=counts, projects=projects, loose_ends=loose_ends, set_aside=set_aside,
                took=round(time.monotonic() - started, 3), paths=paths)


def sources(*, claude_home=CLAUDE_HOME, codex_home=CODEX_HOME, home=None):
    """What the Ask screen shows before anything is looked at: whether each tool's list is on this Mac. Existence only."""
    home = os.path.realpath(str(home or Path.home()))
    return [dict(tool='claude', path=tilde(Path(claude_home) / 'projects', home), present=(Path(claude_home) / 'projects').is_dir()),
            dict(tool='codex', path=tilde(Path(codex_home), home), present=(Path(codex_home) / CODEX_STATE).is_file())]
