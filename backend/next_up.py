"""Next up: one living plan per project (beta.16, b16/lifecycle; Bryce, 24 Sep 2026).

The in-app Dervo reports and Bugs and ideas screens go. Each project gets one plan kept as ``NEXT-UP.md`` at its root,
the way the beta plan files work, so agents in Claude Code and Codex can read and update it like any other file, and
Dervo shows it as the Next up page. The file is the record. This module reads it into sections and makes the few edits
the page offers (add, move, ship, drop), each against the revision the page last read, so an edit an agent made in the
meantime is never overwritten.

The sections, in order: **Next** (decided, not started, numbered), **In progress** (with the thread working on it),
**Not decided** (from ⌘⇧B, Home's "log this" and the page's add line, with what was sent with it), and one
**Shipped in <version>** section per release, newest first, so shipped items move down instead of piling up (app #16).
Any other heading an agent adds is kept where it is and left alone.

A line looks like ``- **Title** · 25 Sep · #37``: the title, the day it was added and, for an item that came from the
old Bugs and ideas records, its number. Numbers are per project, so a Dervo report filed from another project into the
reports project's plan says whose number it is: ``#21 in dd`` (behaviour review M1, 25 Sep). The indented lines under
it are its detail: ``Thread: <name>`` names the thread
on it, ``Sent with it: Screen: Settings · Build: 0.1.0-beta.15`` carries the capture's context, and anything else is a
note. Agents can write it by hand and the page still reads it.

Tester reports (admins only) come from the hosted inbox through ``dervo_admin_feedback``; which reports you've seen or
added is kept on this Mac only. Nothing here sends anything anywhere or runs anything.
"""
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from dervo_connections import Rejected

FILE_NAME = 'NEXT-UP.md'
SIZE_LIMIT = 256 * 1024
TITLE_LIMIT = 120
NOTE_LIMIT = 2000
VERSION_LIMIT = 40
KINDS = ('next', 'progress', 'undecided')
HEADINGS = {'next': 'Next', 'progress': 'In progress', 'undecided': 'Not decided'}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
DOT = ' · '
LEAD = ('What’s next for this project, in one plan. Dervo shows this file as its Next up page, and agents in Claude Code '
        'and Codex read it and keep it current too: decided work goes under Next, work under way under In progress with '
        'its thread, finished work under Shipped in its version, and anything not decided yet under Not decided.')

# Operations, registered through project_lifecycle.FIELDS so they reach the host like every lifecycle operation.
FIELDS = {
    'next_up_read': {'projectId'},
    'next_up_add': {'projectId', 'expectedRevision', 'title', 'note'},
    'next_up_move': {'projectId', 'expectedRevision', 'key', 'to'},
    'next_up_ship': {'projectId', 'expectedRevision', 'key', 'version'},
    'next_up_work': {'projectId', 'expectedRevision', 'key'},
    'next_up_reports_here': {'projectId', 'here'},
    'tester_reports': {'reload'},
    'tester_report_mark': {'reportId', 'seen'},
    'tester_report_add': {'projectId', 'expectedRevision', 'reportId'},
}
NO_PROJECT = ('tester_reports', 'tester_report_mark')


def schema(db):
    # Which tester reports you've seen, or added to Next up (and under which title), on this Mac only.
    db.execute('CREATE TABLE IF NOT EXISTS tester_report_marks (report_id TEXT PRIMARY KEY, seen_at INTEGER, '
               'added_at INTEGER, title TEXT)')
    # The one project whose Next up shows tester reports and takes Dervo reports (key 'reports_project').
    db.execute('CREATE TABLE IF NOT EXISTS next_up_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')


# ---------------------------------------------------------------------------------------------- reading the file
ITEM = re.compile(r'^(?:[-*+]|(\d{1,3})[.)])\s+(.*)$')
HEADING = re.compile(r'^##\s+(.+?)\s*#*\s*$')
DAY = re.compile(r'^(?:\d{1,2} (?:' + '|'.join(MONTHS) + r')(?: \d{4})?|\d{4}-\d{2}-\d{2}|Today|Yesterday)$')
# ``#21``, or ``#21 in dd`` for a record another project numbered.
NUMBER = re.compile(r'^#(\d{1,6})(?: in (\S.{0,79}))?$')


def day(at=None):
    when = time.localtime(at if at is not None else time.time())
    return f'{when.tm_mday} {MONTHS[when.tm_mon - 1]}'


def plain(text):
    """A title as written, without the bold or a checkbox around it."""
    text = re.sub(r'^\[[ xX]\]\s+', '', text.strip())
    for mark in ('**', '__'):
        if text.startswith(mark) and text.endswith(mark) and len(text) > 2 * len(mark):
            text = text[len(mark):-len(mark)]
    return text.strip()


def section_of(heading):
    """(kind, version, day) for a heading: one of KINDS, 'shipped' with its version, or 'other'."""
    text = heading.strip()
    low = text.casefold()
    if low.startswith('shipped'):
        rest = text[len('shipped'):].strip()
        parts = rest.split(DOT.strip())
        head = parts[0].strip()
        version = head[3:].strip() if head.casefold().startswith('in ') else head
        return 'shipped', version or 'earlier', (parts[1].strip() if len(parts) > 1 else None)
    for kind, name in (('progress', 'in progress'), ('undecided', 'not decided'), ('next', 'next')):
        if low == name or low.startswith(name + ' ') or low.startswith(name + ':'):
            return kind, None, None
    return 'other', None, None


def parse_item(lines):
    """One list item from its lines: title, day, number, other marks, the thread, what was sent with it and notes."""
    first = ITEM.match(lines[0]).group(2)
    parts = [part.strip() for part in first.split(DOT.strip())]
    title = plain(parts[0])
    found = dict(title=title, day=None, number=None, project=None, marks=[], thread=None, sent=[], notes=[])
    for part in parts[1:]:
        if DAY.match(part) and found['day'] is None:
            found['day'] = part
        elif NUMBER.match(part) and found['number'] is None:
            number = NUMBER.match(part)
            found['number'] = int(number.group(1))
            found['project'] = number.group(2).strip() if number.group(2) else None
        elif part:
            found['marks'].append(part)
    for line in lines[1:]:
        text = line.strip()
        if not text:
            continue
        low = text.casefold()
        if low.startswith('thread:'):
            found['thread'] = text[7:].strip() or None
        elif low.startswith('sent with it:'):
            for pair in text[13:].split(DOT.strip()):
                key, _, value = pair.partition(':')
                if key.strip() and value.strip():
                    found['sent'].append([key.strip(), value.strip()])
        else:
            found['notes'].append(text[2:].strip() if text.startswith(('- ', '* ')) else text)
    return found


class Plan:
    """The file as blocks: the lines before the first ``##`` heading, then each section with its heading, the text
    that isn't a list item, and its items as the lines they were written in. Untouched parts are written back as read."""

    def __init__(self, text):
        self.text = text
        self.head, self.sections = [], []
        section = None
        item = None
        for line in text.splitlines():
            heading = HEADING.match(line)
            if heading:
                kind, version, when = section_of(heading.group(1))
                section = dict(heading=line, kind=kind, version=version, day=when, intro=[], items=[])
                self.sections.append(section)
                item = None
                continue
            if section is None:
                self.head.append(line)
                continue
            if ITEM.match(line):
                item = [line]
                section['items'].append(item)
            elif item is not None and (line.startswith(('  ', '\t')) or not line.strip()):
                item.append(line)
            else:
                item = None
                section['intro'].append(line)
        for section in self.sections:
            for item in section['items']:
                while item and not item[-1].strip():
                    item.pop()

    def entries(self):
        """Every item with its key: the section, the version for a Shipped one, and the title, so a key keeps meaning the
        same line after an agent reorders the file. A repeat of the same title in the same section gets its count."""
        seen = {}
        for section in self.sections:
            for index, lines in enumerate(section['items']):
                item = parse_item(lines)
                base = '\0'.join([section['kind'], section['version'] or '', item['title'].casefold()])
                count = seen[base] = seen.get(base, 0) + 1
                key = hashlib.sha256(f'{base}\0{count}'.encode()).hexdigest()[:16]
                yield section, index, dict(item, key=key)

    def find(self, key):
        for section, index, item in self.entries():
            if item['key'] == key:
                return section, index, item
        raise Rejected('That line isn’t in NEXT-UP.md any more. Reload Next up and try again.')

    def numbers(self):
        """{(number, project)}: project is None for this plan's own records, else the project that numbered it."""
        return {(item['number'], item['project']) for _, _, item in self.entries() if item['number'] is not None}

    def section(self, kind, version=None, day_text=None):
        """The section to add to, made in its place when it's missing: Next, In progress and Not decided in that order
        ahead of any Shipped section; a new Shipped section goes above the older ones."""
        for section in self.sections:
            if section['kind'] == kind and (kind != 'shipped' or section['version'].casefold() == (version or '').casefold()):
                return section
        heading = f'## Shipped in {version}' + (f'{DOT}{day_text}' if day_text else '') if kind == 'shipped' else f'## {HEADINGS[kind]}'
        made = dict(heading=heading, kind=kind, version=version, day=day_text, intro=[], items=[])
        order = list(KINDS) + ['shipped']
        place = len(self.sections)
        for index, other in enumerate(self.sections):
            if other['kind'] in order and order.index(other['kind']) >= order.index(kind):
                place = index
                break
        self.sections.insert(place, made)
        return made

    def render(self):
        head = list(self.head)
        while head and not head[-1].strip():
            head.pop()
        out = head + ([''] if head else [])
        for section in self.sections:
            if section['kind'] == 'next':
                section['items'] = [renumber(lines, index + 1) for index, lines in enumerate(section['items'])]
            intro = list(section['intro'])
            while intro and not intro[0].strip():
                intro.pop(0)
            while intro and not intro[-1].strip():
                intro.pop()
            out += [section['heading'], '']
            if intro:
                out += intro + ['']
            for lines in section['items']:
                out += lines
            if section['items']:
                out.append('')
        while out and not out[-1].strip():
            out.pop()
        return '\n'.join(out) + '\n'


def renumber(lines, number):
    match = ITEM.match(lines[0])
    return [f'{number}. {match.group(2)}'] + lines[1:]


def bullet(lines):
    match = ITEM.match(lines[0])
    return [f'- {match.group(2)}'] + lines[1:]


def item_lines(title, *, when=None, number=None, project=None, marks=(), thread=None, sent=(), notes=()):
    """A new list item, as the page writes one. ``project`` names whose number it is when it isn't this plan's."""
    first = '- **' + title.replace('**', '') + '**' + ''.join(DOT + part for part in [when, *marks] if part)
    if number is not None:
        first += f'{DOT}#{number}' + (f' in {project}' if project else '')
    lines = [first]
    for note in notes:
        lines += ['  ' + line for line in note.splitlines() if line.strip()]
    if thread:
        lines.append(f'  Thread: {thread}')
    if sent:
        lines.append('  Sent with it: ' + DOT.join(f'{key}: {value}' for key, value in sent))
    return lines


def with_thread(lines, thread):
    kept = [line for line in lines[1:] if not line.strip().casefold().startswith('thread:')]
    return [lines[0], *kept, f'  Thread: {thread}']


# ---------------------------------------------------------------------------------------------- the file on disk
def plan_path(host, pid):
    row = host.store.db.execute('SELECT cwd FROM projects WHERE id=?', (pid,)).fetchone()
    if not row:
        raise Rejected('Unknown project')
    folder = Path(row[0])
    if not folder.is_absolute() or not folder.is_dir():
        raise Rejected('The project folder isn’t there any more. It may have been moved or renamed.')
    return folder / FILE_NAME


def read_file(path):
    """(text, revision, modified) for the plan, or ('', None, None) when there's none yet. A link or anything that
    isn't a regular file is refused: Dervo only ever reads and writes the project's own NEXT-UP.md."""
    if path.is_symlink():
        raise Rejected(f'{FILE_NAME} is a link, so Dervo won’t read or change it.')
    if not path.exists():
        return '', None, None
    if not path.is_file():
        raise Rejected(f'{FILE_NAME} isn’t a regular file.')
    data = path.read_bytes()
    if len(data) > SIZE_LIMIT:
        raise Rejected(f'{FILE_NAME} is over {SIZE_LIMIT // 1024} KB, so Dervo won’t show it here. Open it in your editor.')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        raise Rejected(f'{FILE_NAME} isn’t UTF-8 text.') from None
    return text, hashlib.sha256(data).hexdigest(), path.stat().st_mtime


def write_file(path, text, expected):
    """Replace the plan in one step, only when it's still the revision the page read (None: it didn't exist yet)."""
    _, revision, _ = read_file(path)
    if revision != expected:
        raise Rejected(f'{FILE_NAME} changed since Next up read it, maybe by an agent. Reload Next up and try again.')
    data = text.encode('utf-8')
    if len(data) > SIZE_LIMIT:
        raise Rejected(f'{FILE_NAME} would be over {SIZE_LIMIT // 1024} KB. Move some Shipped sections out of it first.')
    handle, temporary = tempfile.mkstemp(prefix='.NEXT-UP.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(data)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        else:
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return hashlib.sha256(data).hexdigest()


def new_plan(name):
    return f'# Next up in {name}\n\n{LEAD}\n'


def project_name(db, pid):
    row = db.execute('SELECT p.cwd, n.name FROM projects p LEFT JOIN project_names n ON n.project_id=p.id WHERE p.id=?',
                     (pid,)).fetchone()
    return (row[1] or Path(row[0]).name or row[0]) if row else 'this project'


# ---------------------------------------------------------------------------------------------- the page's view
def view(host, pid):
    db = host.store.db
    path = plan_path(host, pid)
    text, revision, modified = read_file(path)
    plan = Plan(text)
    threads = thread_states(host, pid)
    sections = {kind: [] for kind in KINDS}
    shipped, other = [], []
    for section, _, item in plan.entries():
        if item['thread']:
            item['thread_id'], item['thread_state'] = threads.get(item['thread'].casefold(), (None, None))
        if section['kind'] in sections:
            sections[section['kind']].append(item)
        elif section['kind'] == 'shipped':
            group = next((g for g in shipped if g['version'] == section['version']), None)
            if group is None:
                group = dict(version=section['version'], day=section['day'], items=[])
                shipped.append(group)
            group['items'].append(item)
    for section in plan.sections:
        if section['kind'] == 'other':
            other.append(HEADING.match(section['heading']).group(1))
    reports_here = setting(db, 'reports_project')
    return dict(exists=revision is not None, file=FILE_NAME, revision=revision, modified=modified,
                name=project_name(db, pid), sections=sections, shipped=shipped, other=other,
                reports_here=reports_here == pid,
                reports_chosen=reports_here is not None, admin=admin(host))


def thread_states(host, pid):
    """{name casefolded: (thread id, 'running'|'needs'|None)} for the project's open threads, so a ``Thread:`` line
    links to the thread and says whether it's running. Someone else's private thread is never named."""
    from host import ACTOR
    import conversation_activity
    db = host.store.db
    rows = db.execute('''SELECT t.id, c.name FROM threads t JOIN local_channels c ON c.id=t.id WHERE t.project_id=?
        AND (t.visibility='shared' OR t.owner=?) AND t.id NOT IN (SELECT thread_id FROM conversation_archives)''', (pid, ACTOR)).fetchall()
    conversation_activity.schema(db)
    activity = conversation_activity.summaries(db, [row[0] for row in rows])
    found = {}
    for tid, name in rows:
        if isinstance(name, str) and name.strip():
            item = activity.get(tid) or {}
            found.setdefault(name.strip().casefold(), (tid, 'running' if item.get('running') else item.get('status')))
    return found


def setting(db, key):
    schema(db)
    row = db.execute('SELECT value FROM next_up_settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def admin(host):
    from beta_plan import is_owner
    return is_owner(host)


# ---------------------------------------------------------------------------------------------- captures
# The Bugs and ideas records made before Next up stay behind, unseen (Bryce, 25 Sep: "Leave them behind"). ⌘⇧B and
# Home's "log this" still save a record, for its number and its Undo, and put a line in the plan as well.
def report_parts(text):
    """(what you wrote, [[key, value]]) from a captured item's first note: BugCapture writes the words, then
    "Sent with it:" and one "- Key: value" line each (BugCapture.reportNote)."""
    words, _, sent = (text or '').partition('\n\nSent with it:\n')
    pairs = []
    for line in sent.splitlines():
        line = line.strip().lstrip('-').strip()
        if not line:
            continue
        if line.startswith('Dervo ') and ':' not in line:
            if line != 'Dervo, version unknown':
                pairs.append(['Build', line[6:].strip()])
            continue
        key, _, value = line.partition(':')
        if key.strip() in ('Project', 'Window') or not value.strip():
            continue
        value = re.sub(r'\s*\([A-Za-z0-9_:-]{6,}\)$', '', value.strip())  # the thread's or task's id, for Dervo only
        pairs.append([key.strip(), value])
    return words.strip(), pairs


def number_tag(db, pid):
    """How a line names the project whose number it carries: its name on one line, without the separator dot."""
    return ' '.join(project_name(db, pid).replace(DOT.strip(), '-').split())[:80] or pid[:80]


def capture_target(db, pid, record):
    """(the project whose NEXT-UP.md holds this capture, the tag its number carries there or None). A Dervo report goes
    to the reports project, and its number is tagged with the project that numbered it, since numbers are per project."""
    target = (setting(db, 'reports_project') or pid) if record.get('product') == 'dervo' else pid
    return target, (number_tag(db, pid) if target != pid else None)


def lines_from_item(record, who=None, project=None):
    """A captured record as a plan line, with its number so Undo can find it and it's never added twice. ``project``
    tags the number with the project that gave it, when that isn't the plan's own."""
    notes = [note for note in record.get('notes') or [] if isinstance(note, dict)]
    words, sent = report_parts(notes[0]['text']) if notes else ('', [])
    first = words.split('\n', 1)[0].strip() if words else ''
    rest = [first] if first and first.casefold() != record['name'].casefold() else []
    when = None
    try:
        from datetime import datetime
        when = day(datetime.fromisoformat(record['created_at'].replace('Z', '+00:00')).timestamp())
    except (KeyError, TypeError, ValueError):
        pass
    marks = [who] if who else []
    if record.get('kind') == 'idea':
        marks.insert(0, 'Idea')
    return item_lines(record['name'], when=when, number=record.get('number'), project=project, marks=marks, sent=sent, notes=rest)


def from_capture(host, pid, record, actor):
    """⌘⇧B and Home's "log this" land here after the old record is saved: one dated line under Not decided with what
    was sent with it. A plan that can't be written (a link, too big) keeps the record and says why in the host's log."""
    from conversation_organisation import HOME_ACTOR
    db = host.store.db
    target, tag = capture_target(db, pid, record)
    who = 'Home, from your message' if actor == HOME_ACTOR else 'You, with ⌘⇧B'
    try:
        path = plan_path(host, target)
        text, revision, _ = read_file(path)
        plan = Plan(text or new_plan(project_name(db, target)))
        if (record.get('number'), tag) in plan.numbers():
            return
        plan.section('undecided')['items'].append(lines_from_item(record, who, tag))
        write_file(path, plan.render(), revision)
    except (Rejected, OSError) as exc:
        import sys
        print(f'Next up couldn’t add #{record.get("number")}: {exc}', file=sys.stderr)


def forget_capture(host, pid, record):
    """Undo straight after ⌘⇧B takes its line back out of Not decided, if it's still there as written: the same
    number from the same project, never another project's line that shares the number."""
    db = host.store.db
    target, tag = capture_target(db, pid, record)
    try:
        path = plan_path(host, target)
        text, revision, _ = read_file(path)
        if revision is None:
            return
        plan = Plan(text)
        for section, index, item in list(plan.entries()):
            if section['kind'] == 'undecided' and item['number'] == record.get('number') and item['project'] == tag:
                del section['items'][index]
                write_file(path, plan.render(), revision)
                return
    except (Rejected, OSError):
        pass


# ---------------------------------------------------------------------------------------------- tester reports
class Reports:
    """The admin's tester reports, read in the background like the Testers page's usage and errors."""

    def __init__(self, host):
        self.host = host
        self.state = None
        self.client = None

    def read(self, reload):
        account = getattr(self.host, 'account_session', None)
        if account is None or not hasattr(account, '_require_admin'):
            raise Rejected('Only Dervo admins can see tester reports.')
        session = account._require_admin()
        state = self.state
        if state is None or reload and state['state'] != 'loading':
            state = self.state = {'state': 'loading', 'data': state['data'] if state else None}
            client = account.client
            def work():
                try:
                    state['data'] = client.admin_feedback(session, 30)
                    state['state'] = 'ready'
                except Exception:
                    state['state'] = 'error'
            threading.Thread(target=work, name='dervo-tester-reports', daemon=True).start()
        return state


def reports(host):
    if not hasattr(host, 'tester_reports'):
        host.tester_reports = Reports(host)
    return host.tester_reports


def report_rows(host, data):
    """The reports with what you did about each on this Mac, and whether the Next up item it became has shipped."""
    db = host.store.db
    schema(db)
    marks = {row[0]: row[1:] for row in db.execute('SELECT report_id, seen_at, added_at, title FROM tester_report_marks')}
    shipped = {}
    home = setting(db, 'reports_project')
    if home:
        try:
            plan = Plan(read_file(plan_path(host, home))[0])
            for section, _, item in plan.entries():
                if section['kind'] == 'shipped':
                    shipped.setdefault(item['title'].casefold(), section['version'])
        except (Rejected, OSError):
            pass
    rows = []
    for report in (data or {}).get('reports') or []:
        seen_at, added_at, title = marks.get(report['id'], (None, None, None))
        rows.append(dict(report, seen=seen_at is not None or added_at is not None, added=added_at is not None,
                         shipped_in=shipped.get(title.casefold()) if title else None))
    return rows


# ---------------------------------------------------------------------------------------------- operations
def clean_title(value):
    if type(value) is not str or any(ord(c) < 32 for c in value):
        raise Rejected(f'Give it a title on one line, up to {TITLE_LIMIT} characters.')
    value = ' '.join(value.split()).replace('**', '')
    if not value or len(value) > TITLE_LIMIT:
        raise Rejected(f'Give it a title on one line, up to {TITLE_LIMIT} characters.')
    return value


def _quoted(text):
    """``text`` in a code fence longer than any run of backticks inside it, so a note can't close it early."""
    ticks = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', text)), default=0))
    return f'{ticks}text\n{text}\n{ticks}'


def expected(value):
    if value is not None and (type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value)):
        raise Rejected('Reload Next up and try again.')
    return value


def dispatch(host, operation, args):
    db = host.store.db
    schema(db)
    if operation == 'tester_reports':
        if type(args['reload']) is not bool:
            raise Rejected('Unsupported Next up fields')
        state = reports(host).read(args['reload'])
        rows = report_rows(host, state['data'])
        # ``project`` is where they show (next_up_reports_here), so the sidebar counts them on that project only.
        return dict(state=state['state'], reports=rows, unseen=sum(1 for row in rows if not row['seen']),
                    project=setting(db, 'reports_project'))
    if operation == 'tester_report_mark':
        report_id, seen = args['reportId'], args['seen']
        if type(report_id) is not str or not re.fullmatch(r'[0-9a-f-]{36}', report_id) or type(seen) is not bool:
            raise Rejected('Choose a report.')
        if not admin(host):
            raise Rejected('Only Dervo admins can see tester reports.')
        with db:
            if seen:
                db.execute('INSERT INTO tester_report_marks (report_id, seen_at) VALUES (?,?) ON CONFLICT(report_id) '
                           'DO UPDATE SET seen_at=excluded.seen_at', (report_id, int(time.time())))
            else:
                db.execute('UPDATE tester_report_marks SET seen_at=NULL WHERE report_id=?', (report_id,))
        return {'id': report_id, 'seen': seen}
    pid = args['projectId']
    if type(pid) is not str or not db.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
        raise Rejected('Unknown project')
    if db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (pid,)).fetchone():
        raise Rejected('This project is archived. Restore it before opening or changing its records.')
    if operation == 'next_up_read':
        return view(host, pid)
    if operation == 'next_up_reports_here':
        if type(args['here']) is not bool:
            raise Rejected('Unsupported Next up fields')
        if not admin(host):
            raise Rejected('Only Dervo admins can see tester reports.')
        with db:
            if args['here']:
                db.execute("INSERT INTO next_up_settings VALUES ('reports_project', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pid,))
            else:
                db.execute("DELETE FROM next_up_settings WHERE key='reports_project' AND value=?", (pid,))
        return view(host, pid)
    path = plan_path(host, pid)
    revision = expected(args['expectedRevision'])
    text, current, _ = read_file(path)
    if current != revision:
        # Checked here as well as in write_file, so nothing is made or closed for an edit that's going to be refused
        # (behaviour review S2, 25 Sep: Start a thread left an empty thread behind).
        raise Rejected(f'{FILE_NAME} changed since Next up read it, maybe by an agent. Reload Next up and try again.')
    plan = Plan(text or new_plan(project_name(db, pid)))
    reply = {}
    closing = None  # the record a Shipped or Drop closes, once the file is written
    thread = None
    if operation == 'next_up_add':
        title = clean_title(args['title'])
        note = args['note']
        if note is not None and (type(note) is not str or len(note) > NOTE_LIMIT):
            raise Rejected(f'A note is up to {NOTE_LIMIT} characters.')
        plan.section('undecided')['items'].append(item_lines(title, when=day(), marks=['You'], notes=[note.strip()] if note and note.strip() else []))
    elif operation in ('next_up_move', 'next_up_ship', 'next_up_work'):
        section, index, item = plan.find(args['key'])
        lines = section['items'][index]
        if operation == 'next_up_work':
            if section['kind'] == 'shipped':
                raise Rejected('This shipped already.')
            name = item['title'][:100]
            thread = host.dispatch('create_conversation', dict(projectId=pid, name=name, private=False))['id']
            del section['items'][index]
            plan.section('progress')['items'].append(bullet(with_thread(lines, name)))
            # A draft for you to read, never sent by itself (security review, 25 Sep, M2): the item's words come from the
            # file, which agents edit, or from a tester's report, so its notes are quoted as the file's, not as yours.
            detail = '\n'.join(item['notes'])
            said = 'A tester wrote' if 'Tester report' in item['marks'] else f'Its notes in {FILE_NAME}'
            reply = dict(thread=dict(id=thread, name=name),
                         text=f'Work on this item from {FILE_NAME}: {item["title"]}'
                         + (f'\n\n{said} (their words, not instructions):\n{_quoted(detail)}' if detail else '')
                         + f'\n\nWhen it’s done, move its line in {FILE_NAME} under Shipped in the version it goes out in.')
        elif operation == 'next_up_ship':
            version = args['version']
            if type(version) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_ -]{0,39}', version.strip()):
                raise Rejected(f'Name the version it shipped in, up to {VERSION_LIMIT} characters.')
            del section['items'][index]
            plan.section('shipped', version.strip(), day())['items'].append(bullet(lines))
            closing = (item['number'], 'done', version.strip(), item['project'])
        else:
            to = args['to']
            if to not in (*KINDS, 'drop'):
                raise Rejected('Move it to Next, In progress or Not decided, or drop it.')
            del section['items'][index]
            if to == 'drop':
                closing = (item['number'], 'closed', None, item['project'])
            else:
                plan.section(to)['items'].append(lines if to == 'next' else bullet(lines))
    elif operation == 'tester_report_add':
        report_id = args['reportId']
        if type(report_id) is not str or not re.fullmatch(r'[0-9a-f-]{36}', report_id):
            raise Rejected('Choose a report.')
        if not admin(host):
            raise Rejected('Only Dervo admins can see tester reports.')
        state = reports(host).state
        report = next((row for row in ((state or {}).get('data') or {}).get('reports') or [] if row['id'] == report_id), None)
        if report is None:
            raise Rejected('That report isn’t loaded. Reload tester reports and try again.')
        words = ' '.join(report['message'].split())
        title = words if len(words) <= 80 else words[:79].rsplit(' ', 1)[0] + '…'
        sent = [[key, value] for key, value in (('Tester', report['tester'] or 'A tester'), ('Build', report['app_version']),
                                                ('Screen', report['route']), ('Category', report['category'].capitalize())) if value]
        plan.section('undecided')['items'].append(item_lines(clean_title(title), when=day(report['created_at']), marks=['Tester report'],
                                                             sent=sent, notes=[report['message']] if words != title else []))
        reply = {'reportId': report_id, 'title': title}
    else:
        raise Rejected('Unsupported Next up operation')
    try:
        write_file(path, plan.render(), revision)
    except (Rejected, OSError):
        if thread:
            # The file changed in the moment between the check above and the write: take the new, empty thread back out.
            try:
                host.dispatch('conversation_delete', dict(projectId=pid, channelId=thread, confirmation='DELETE ' + thread))
            except Rejected:
                pass
        raise
    if closing:
        close_item(host, pid, *closing)
    if operation == 'tester_report_add':
        with db:
            db.execute('INSERT INTO tester_report_marks (report_id, seen_at, added_at, title) VALUES (?,?,?,?) ON CONFLICT(report_id) '
                       'DO UPDATE SET added_at=excluded.added_at, title=excluded.title, seen_at=coalesce(seen_at, excluded.seen_at)',
                       (reply['reportId'], int(time.time()), int(time.time()), reply['title']))
    return dict(view(host, pid), **reply)


def close_item(host, pid, number, state, version, project=None):
    """A captured line that ships or is dropped closes its record too, so the record says what became of it. A line
    tagged with another project (``#21 in dd``) closes that project's Dervo report, found by its tag. When the tag names
    no project, or more than one project has that record, nothing is closed rather than the wrong one."""
    if number is None:
        return
    from project_ideas import ProjectIdeas
    db = host.store.db
    ideas = ProjectIdeas(host.store, host)
    if project is None:
        owners = [pid]
    else:
        owners = [row[0] for row in db.execute('SELECT id FROM projects WHERE id<>?', (pid,)) if number_tag(db, row[0]) == project]
    found = [(owner, item) for owner in owners for item in ideas.items(owner)
             if item.get('number') == number and not item.get('archived_at') and (project is None or item.get('product') == 'dervo')]
    if len(found) != 1:
        return
    owner, record = found[0]
    if record['state'] == state:
        return
    profile = db.execute('SELECT profile_id FROM projects WHERE id=?', (owner,)).fetchone()[0]
    try:
        ideas.item_operation({'project_id': owner, 'profile_id': profile}, 'ideas_item_set',
                             dict(itemId=record['id'], expectedRevision=record['revision'], name=record['name'], state=state,
                                  target=version[:32] if version else record.get('target')), 'local-user')
    except Rejected:
        pass
