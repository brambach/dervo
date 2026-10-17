"""What Dervo would create for one project folder from the Claude Code and Codex history it already has. Prints, and
writes nothing: not to ``~/.claude``, not to ``~/.codex``, not to any Dervo store.

    python3 tools/import_dry_run.py ~/dervo
    python3 tools/import_dry_run.py ~/dervo --all        # file the finished threads too
    python3 tools/import_dry_run.py ~/dervo --store      # how many of these Dervo already has

The sorting is the app's own (``backend/history_import.plan``): ``conversation_organisation.suggest`` names each open
thread from its first message and ``project_ideas.file_by_rules`` files it, in a temporary store that is deleted when
this exits. The rule beside each thread is the rung of the ladder that placed it.

``--store`` measures the overlap with the live Dervo store (Bryce, 21 Sep: the blank project that prompted this had
every thread archived, so an import that doesn't check would bring them all back). The store is read through a
temporary copy, the way ``tools/jev_offtrack.py`` does it, and never opened live. Two keys are tried: the Claude
session id, which ``threads.session_id`` and each coding task record can carry, and the first message you typed,
compared after folding case and spaces. Dervo's own transcripts live under its profile homes in Application
Support, not under ``~/.claude``, so an id match would mean the same session was launched from both places.

Thread names printed here come from the title the person's own tool kept (Claude Code's ai-title, Codex's name),
tidied by ``thread_names.tidy_title``; a session with no such title is named from its first message by the rules. The
name the first message would have given is printed beside each for comparison. No message text is printed. With
``--store``, the idle rule also measures from the store's newest activity on the project, so a folder whose work moved
into Dervo doesn't read its last terminal sessions as open.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))

import history_import  # noqa: E402

LIVE_STORE = Path.home() / 'Library/Application Support/local.dervo.connected/conversations.sqlite3'
HOME = str(Path.home())


def short(path):
    return str(path).replace(HOME, '~')


def day(stamp):
    return datetime.fromtimestamp(stamp).strftime('%d %b') if stamp else '?'


def normalised(text):
    return ' '.join((text or '').casefold().split())[:200]


def read_only_copy(path, into):
    """A read-only connection to a copy of the store (and its WAL, or the copy would miss the newest rows)."""
    copy = Path(into) / 'conversations.sqlite3'
    shutil.copy2(path, copy)
    for extra in ('-wal', '-shm'):
        source = path.parent / (path.name + extra)
        if source.exists():
            shutil.copy2(source, copy.parent / (copy.name + extra))
    db = sqlite3.connect(f'file:{copy}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def store_project(db, cwd):
    """The store's project row for this folder, or None."""
    real = os.path.realpath(cwd)
    return next((dict(row) for row in db.execute('SELECT id, cwd, profile_id FROM projects')
                 if os.path.realpath(row['cwd']) == real), None)


def newest_activity(db, project_id):
    """When the store last saw work on the project, in seconds: a thread opened, a task changed, a message written or
    a thread archived. None when it has nothing for the project."""
    stamps = []
    for table, column, sql in (
            ('conversation_seen', 'seen', 'SELECT max(s.seen) FROM conversation_seen s JOIN threads t ON t.id=s.thread_id WHERE t.project_id=?'),
            ('conversation_task_state', 'changed', 'SELECT max(s.changed) FROM conversation_task_state s JOIN threads t ON t.id=s.thread_id WHERE t.project_id=?'),
            ('local_projection', 'created_at', 'SELECT max(p.created_at) FROM local_projection p JOIN messages m ON m.id=p.id JOIN threads t ON t.id=m.thread_id WHERE t.project_id=?'),
            ('conversation_archives', 'archived_at', 'SELECT max(a.archived_at) FROM conversation_archives a JOIN threads t ON t.id=a.thread_id WHERE t.project_id=?')):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            value = db.execute(sql, (project_id,)).fetchone()[0]
            if value:
                stamps.append(float(value))
    return max(stamps) if stamps else None


def overlap(db, store_path, cwd, sessions):
    """How many imported sessions Dervo already holds for this folder, by session id and by first message."""
    out = dict(store=short(store_path), project=None)
    out['projects'] = [short(row['cwd']) for row in db.execute('SELECT cwd FROM projects')]
    project = store_project(db, cwd)
    if project is None:
        out['note'] = 'this folder is not a project in the live store'
        return out
    out['newest'] = newest_activity(db, project['id'])
    out['project'] = short(project['cwd'])
    threads = [dict(row) for row in db.execute('SELECT id, session_id FROM threads WHERE project_id=?', (project['id'],))]
    archived = {row[0] for row in db.execute('SELECT thread_id FROM conversation_archives')}
    out['threads'] = len(threads)
    out['archived'] = sum(1 for t in threads if t['id'] in archived)
    ids = {t['session_id'] for t in threads if t['session_id']}
    firsts = {}
    has_tasks = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone()
    if has_tasks:
        for (document,) in db.execute('SELECT document FROM coding_tasks'):
            try:
                task = json.loads(document)
            except ValueError:
                continue
            context = task.get('context') or {}
            if context.get('project_id') != project['id']:
                continue
            for key in ('session_id', 'guide_session_id', 'memory_session_id'):
                if task.get(key):
                    ids.add(task[key])
            for run in task.get('runs') or []:
                if isinstance(run, dict) and run.get('session_id'):
                    ids.add(run['session_id'])
            for message in task.get('messages') or []:
                if isinstance(message, dict) and message.get('role') == 'user' and str(message.get('text', '')).strip():
                    firsts.setdefault(context.get('thread_id'), normalised(message['text']))
                    break
    out['store_session_ids'] = len(ids)
    out['store_first_messages'] = len(firsts)
    imported = [s for s in sessions if s.state != 'skipped']
    out['by_session_id'] = sum(1 for s in imported if s.id in ids)
    texts = set(firsts.values())
    out['by_first_message'] = sum(1 for s in imported if normalised(s.first_text) in texts)
    # Where Dervo's own transcripts for this folder live: its profile homes, never ~/.claude.
    homes = [row[0] for row in db.execute('SELECT home FROM profiles')]
    own = 0
    for home in homes:
        folder = history_import.claude_project_dir(cwd, home)
        if folder.is_dir():
            own += sum(1 for p in folder.iterdir() if p.suffix == '.jsonl')
    out['own_transcripts'] = own
    return out


def report(cwd, result, elapsed, include_finished, found=None, claude_home=None, codex_home=None, out=None):
    counts, sessions = result['counts'], result['sessions']
    claude_home = Path(claude_home or history_import.CLAUDE_HOME)
    codex_home = Path(codex_home or history_import.CODEX_HOME)
    say = lambda line='': print(line, file=out or sys.stdout)
    say(f'Import dry run for {short(cwd)}   (nothing written)')
    say(f"Claude Code: {short(history_import.claude_project_dir(cwd, claude_home))}: {counts['claude']} sessions, "
        f"{counts['claude_extras']} subagent transcripts beside them")
    say(f"Codex: {counts['codex']} threads for this folder in {short(codex_home / history_import.CODEX_STATE)}")
    say(f"Read in {elapsed:.1f} s. Newest activity {day(result['latest'])}.")
    say()
    say(f"Would create {counts['workstreams']} workstreams holding {counts['filed']} threads"
        + (' (open and finished)' if include_finished else ' (open only)') + ':')
    for stream in result['workstreams']:
        say()
        say(f"  {stream['name']}  ({len(stream['threads'])})")
        for s in stream['threads']:
            was = s.opener_name if s.opener_name != s.name else None
            before = f"   [from the first message it was: {was or 'abstained'}]" if s.named_by != 'first message' else ''
            say(f"    - {s.name:<40} {s.rule:<26} {s.named_by:<14} {s.state}, {s.reason}, {day(s.last_at)}  "
                f"{s.provider} {s.id[:8]}{before}")
    loose = [s for s in sessions if s.state == 'open' and not s.group]
    if loose:
        say()
        say(f'Open but not filed ({len(loose)}): no title from the tool, and the naming rules abstained on the first message, '
            f'as they would in the app.')
        for s in loose:
            say(f"    - {s.provider} {s.id[:8]}  {s.reason}, {day(s.last_at)}, {len(s.first_text)} chars: "
                f"{history_import.why_abstained(s.first_text)}")
    by = counts['named_by']
    say()
    say(f"Named from: ai-title {by['ai-title']}, custom-title {by['custom-title']}, Codex name {by['codex name']}, "
        f"first message {by['first message']}; no name {counts['abstained']}.")
    finished = [s for s in sessions if s.state == 'finished' and not s.group]
    if finished:
        say()
        say(f'Finished, left out ({len(finished)}):')
        for s in finished:
            label = s.name or s.title or '(no name)'
            say(f"    - {label:<40} {s.reason}, {day(s.last_at)}  {s.provider} {s.id[:8]}")
    skipped = [s for s in sessions if s.state == 'skipped']
    if skipped:
        say()
        say(f'Skipped ({len(skipped)}):')
        reasons = {}
        for s in skipped:
            reasons.setdefault((s.provider, s.reason), []).append(s.id[:8])
        for (provider, reason), ids in sorted(reasons.items()):
            say(f"    - {provider}, {reason}: {len(ids)}" + (f" ({', '.join(ids)})" if len(ids) <= 4 else ''))
    say()
    say(f"{counts['transcripts']} transcripts read. {counts['sessions']} sessions you started "
        f"({counts['open']} open, {counts['finished']} finished). {counts['workstreams']} things being worked on.")
    if found is not None:
        say()
        say(f"Already in Dervo ({found['store']}):")
        if found.get('error') or found.get('note'):
            say(f"    {found.get('error') or found.get('note')}. Projects there: {', '.join(found.get('projects') or []) or 'none'}")
        else:
            say(f"    project {found['project']}: {found['threads']} threads, {found['archived']} archived; "
                f"{found['store_session_ids']} session ids on record, {found['store_first_messages']} first messages; "
                f"newest activity there {day(found.get('newest'))}, and the idle rule measured from it")
            say(f"    imported sessions matching by session id: {found['by_session_id']}")
            say(f"    imported sessions matching by first message: {found['by_first_message']}")
            say(f"    Dervo's own transcripts for this folder under its profile homes: {found['own_transcripts']} (not read by the import)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('folder')
    parser.add_argument('--all', action='store_true', help='file finished threads too')
    parser.add_argument('--store', nargs='?', const=str(LIVE_STORE), default=None,
                        help='measure the overlap with a Dervo store (default: the live one, read through a copy)')
    parser.add_argument('--claude-home', default=str(history_import.CLAUDE_HOME))
    parser.add_argument('--codex-home', default=str(history_import.CODEX_HOME))
    args = parser.parse_args(argv)
    cwd = os.path.realpath(args.folder)
    if not os.path.isdir(cwd):
        raise SystemExit(f'Not a folder: {args.folder}')
    scratch = tempfile.mkdtemp(prefix='dervo-import-dry-run-')
    copy_dir, plan_dir = Path(scratch) / 'store-copy', Path(scratch) / 'plan'  # the copy and the plan's own store, apart
    copy_dir.mkdir()
    plan_dir.mkdir()
    db, found, floor = None, None, None
    try:
        if args.store:
            if not Path(args.store).is_file():
                found = dict(store=short(args.store), error='no store at that path')
            else:
                db = read_only_copy(Path(args.store), copy_dir)
                project = store_project(db, cwd)
                floor = newest_activity(db, project['id']) if project else None
        started = time.monotonic()
        result = history_import.plan(cwd, plan_dir, claude_home=Path(args.claude_home), codex_home=Path(args.codex_home),
                                     include_finished=args.all, latest_floor=floor)
        elapsed = time.monotonic() - started
        if db is not None:
            found = overlap(db, Path(args.store), cwd, result['sessions'])
        report(cwd, result, elapsed, args.all, found, args.claude_home, args.codex_home)
    finally:
        if db is not None:
            db.close()
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
