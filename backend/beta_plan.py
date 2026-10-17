"""The beta you're running, what went into it, and what the next one is meant to hold.

Three sources, and none is a guess. ``runtime/dervo-beta-plan.json`` holds the versions that were built, the commit
each was built from and the headline. The release builder adds each build to it (``stamp``); headlines are written by hand. What the next version is meant to hold comes from the ideas in Dervo's
own project whose target is that version, so planning in the project room is planning the release; the file's own list
is kept only for anything no idea covers. Everything else is read from Dervo's own checkout with git, so "what's in
this beta" is the list of commits that were actually in it, not a description that can drift. A build carries the plan file, so a packaged app shows its own version and plan even when
no checkout is around; the commit lists appear when Dervo's source project is open in this workspace.
"""
import json
from pathlib import Path
import re
import subprocess

from dervo_connections import Rejected
from release_preview import running_release

FIELDS = {'beta_plan': set()}
PLAN = 'runtime/dervo-beta-plan.json'
LINES = 12
SHA = re.compile(r'[0-9a-f]{7,40}')
STATES = {'planned', 'building', 'done'}


def read_plan(resources=None):
    """The hand-written record: shipped versions newest first, and the next version's intended features."""
    resources = Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
    try:
        record = json.loads((resources / PLAN).read_text())
    except (OSError, ValueError):
        raise Rejected('This build doesn’t carry a beta plan.') from None
    if type(record) is not dict or record.get('schema') != 1 or type(record.get('shipped')) is not list:
        raise Rejected('The beta plan isn’t in a supported format.')
    plan = record.get('next')
    if type(plan) is not dict or type(plan.get('version')) is not str or type(plan.get('planned')) is not list:
        raise Rejected('The beta plan needs a next version and its intended features.')
    for item in plan['planned']:
        if type(item) is not dict or type(item.get('title')) is not str or item.get('state') not in STATES:
            raise Rejected('Each planned feature needs a title and a state of planned, building or done.')
    for entry in record['shipped']:
        if type(entry) is not dict or type(entry.get('version')) is not str or not SHA.fullmatch(str(entry.get('commit'))):
            raise Rejected('Each shipped version needs its version and the commit it was built from.')
    previews = record.get('previews', [])
    if type(previews) is not list or any(type(entry) is not dict or type(entry.get('version')) is not str
                                         or not SHA.fullmatch(str(entry.get('commit'))) for entry in previews):
        raise Rejected('Each preview build needs its version and the commit it was built from.')
    return record


PREVIEW = '-preview.'


def is_preview(version):
    """A local preview build such as 0.1.0-beta.11-preview.10. Testers never get one, so it never counts as shipped."""
    return PREVIEW in version


def release_of(version):
    """The real version a preview was building towards: 0.1.0-beta.11-preview.10 is 0.1.0-beta.11."""
    return version.split(PREVIEW, 1)[0]


def tidy(record):
    """Keep preview builds out of shipped and out of next. Builders before 20 Sep recorded every preview as shipped
    and bumped next to the preview after it, so a plan written then reads the same as one written now: the preview
    entries move to their own list, and a next version that is a preview becomes the release it was building towards
    (or the one after that, if that release has shipped). Changes the record in place and returns it."""
    shipped = record['shipped']
    moved = [entry for entry in shipped if is_preview(entry['version'])]
    if moved:
        record['shipped'] = [entry for entry in shipped if not is_preview(entry['version'])]
        known = {entry['version'] for entry in record.get('previews', [])}
        record['previews'] = [entry for entry in moved if entry['version'] not in known] + record.get('previews', [])
    plan = record['next']
    if is_preview(plan['version']):
        version = release_of(plan['version'])
        if any(entry['version'] == version for entry in record['shipped']):
            version = successor(version) or version
        record['next'] = {**plan, 'version': version}
    return record


def successor(version):
    """0.1.0-beta.10 as 0.1.0-beta.11. A version that doesn't end in a number has no obvious next one."""
    match = re.fullmatch(r'(.*?)(\d+)', version)
    return f'{match[1]}{int(match[2]) + 1}' if match else None


def stamp(work, source, release_id, built):
    """Record the build being made in its own plan file, so the page it ships with names it and what comes after it.

    The builder calls this on its snapshot, where ``work`` holds the plan file that goes into the bundle and ``source``
    is the checkout the snapshot came from. The build becomes the newest shipped entry, at the commit the checkout was
    on, and the next version becomes the one after it. The file's plan for the next version carries over only when it
    already names that version; a plan for the version being built is now this build, and a plan for an older one is
    stale. A preview build (``is_preview``) goes to its own ``previews`` list instead and leaves shipped and next as
    they were, because no tester ever gets one. Returns the stamped record, or None when the snapshot has no plan file
    or the checkout has no commit."""
    path = Path(work) / PLAN
    if not path.is_file():
        return None
    record = tidy(read_plan(work))
    if any(entry['version'] == release_id for entry in record['shipped'] + record.get('previews', [])):
        raise Rejected(f'The beta plan already lists {release_id}. Choose a new release ID.')
    head = _git(source, ['rev-parse', '--short=7', 'HEAD'])
    if head is None or not SHA.fullmatch(head.strip()):
        return None
    if is_preview(release_id):
        record['previews'] = [{'version': release_id, 'built': built, 'commit': head.strip()}] + record.get('previews', [])
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
        return record
    plan = record['next']
    record['shipped'].insert(0, {'version': release_id, 'built': built, 'commit': head.strip(),
                                 'headline': plan.get('headline') if plan['version'] == release_id else None})
    following = successor(release_id)
    if following and plan['version'] != following:
        record['next'] = {'version': following, 'headline': None, 'planned': []}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return record


def source_repo(host, resources):
    """Dervo's own checkout: this source tree when Dervo runs from it, else the project in this workspace that builds it."""
    if (resources / '.git').exists():
        return resources
    try:
        rows = host.store.db.execute('SELECT cwd FROM projects').fetchall()
    except Exception:
        return None
    for row in rows:
        folder = Path(row['cwd'] if not isinstance(row, tuple) else row[0])
        try:
            identifier = json.loads((folder / 'tauri.conf.json').read_text()).get('identifier')
        except (OSError, ValueError, TypeError):
            continue
        if identifier == 'local.dervo.connected' and (folder / '.git').exists():
            return folder
    return None


def _git(repo, arguments):
    """Read-only git in Dervo's checkout, through task_git._git (SAFE pins, the checkout's own filters off). A missing
    commit or a folder that isn't a repository reads as nothing."""
    import task_git
    try:
        done = task_git._git(str(repo), *arguments, timeout=15)
    except (OSError, subprocess.SubprocessError, Rejected):
        return None
    return done.stdout if done.returncode == 0 else None


def _range(older, newer):
    """A git range from plan-supplied commits. Only 'HEAD' and plain commit ids reach the command line."""
    for value in (older, newer):
        if value is not None and value != 'HEAD' and not SHA.fullmatch(value):
            raise Rejected('The beta plan names a commit that isn’t a commit.')
    return f'{older}..{newer}' if older else newer


def commits(repo, spec, limit=LINES):
    """The commits of one version, newest first, with how many there are in all."""
    if repo is None:
        return [], 0
    text = _git(repo, ['log', '--no-color', f'--pretty=format:%h\x1f%ad\x1f%s', '--date=short', '-n', str(limit), spec, '--'])
    if text is None:
        return [], 0
    rows = []
    for line in text.splitlines():
        parts = line.split('\x1f')
        if len(parts) == 3:
            rows.append({'sha': parts[0], 'date': parts[1], 'title': parts[2][:200]})
    total = _git(repo, ['rev-list', '--count', spec, '--'])
    return rows, int(total.strip()) if total and total.strip().isdigit() else len(rows)


OWNER_ONLY = 'Releases is for the Dervo team during the beta.'


def is_owner(host):
    """A signed-in Dervo admin, or a build with no Dervo sign-in at all (a source checkout: no session yet, or the
    placeholder session that reports sign-in as unavailable). Every packaged build carries sign-in, so a tester is
    always a signed-in account and always checked against its membership."""
    session = getattr(host, 'account_session', None)
    if session is None or getattr(session, 'state', None) == 'unavailable':
        return True
    return (getattr(session, 'membership', None) or {}).get('is_admin') is True


def planned_from_ideas(host, repo, version):
    """The ideas of Dervo's own project aimed at this version, in the order they were made. An idea is a plan entry
    with its conversations counted, so the page can say how much work is behind it. Nothing here reads a conversation."""
    if repo is None:
        return []
    try:
        project = next((row for row in host.store.db.execute('SELECT id, cwd, profile_id FROM projects').fetchall()
                        if Path(row['cwd']).resolve() == Path(repo).resolve()), None)
    except Exception:
        return []
    if project is None:
        return []
    from project_ideas import ProjectIdeas
    listing = ProjectIdeas(host.store).listing(dict(project_id=project['id'], profile_id=project['profile_id']))
    return [{'title': idea['name'], 'detail': idea['summary'] or None, 'state': idea['state'], 'source': 'idea',
             'conversations': len(idea['conversations'])}
            for idea in listing['ideas'] if idea['target'] == version]


def view(host, resources=None):
    """What the Updates page shows: this version, the versions before it, and what the next one is for."""
    resources = Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
    record = tidy(read_plan(resources))  # preview builds are Bryce's own, never a release, so the page leaves them out
    repo = source_repo(host, resources)
    shipped = []
    for index, entry in enumerate(record['shipped']):
        older = record['shipped'][index + 1]['commit'] if index + 1 < len(record['shipped']) else entry.get('from')
        rows, total = commits(repo, _range(older if type(older) is str else None, entry['commit']))
        shipped.append({'version': entry['version'], 'built': entry.get('built') or None,
                        'headline': entry.get('headline') or None, 'commit': entry['commit'],
                        'features': rows, 'total': total})
    newest = record['shipped'][0]['commit'] if record['shipped'] else None
    landed, total = commits(repo, _range(newest, 'HEAD')) if newest else ([], 0)
    status = _git(repo, ['status', '--porcelain']) if repo else None
    running = running_release(resources)
    version = record['next']['version']
    from_ideas = planned_from_ideas(host, repo, version)
    named = {item['title'].casefold() for item in from_ideas}
    from_file = [{'title': item['title'], 'detail': item.get('detail') or None, 'state': item['state'], 'source': 'file',
                  'conversations': None}
                 for item in record['next']['planned'] if item['title'].casefold() not in named]
    return {'running': running, 'source': str(repo) if repo else None,
            'next': {'version': version, 'headline': record['next'].get('headline') or None,
                     'planned': from_ideas + from_file,
                     'landed': landed, 'total': total,
                     'uncommitted': len([line for line in status.splitlines() if line.strip()]) if status is not None else None},
            'shipped': shipped, 'file': PLAN}


def dispatch(host, operation, args):
    if operation != 'beta_plan' or type(args) is not dict or args:
        raise Rejected('Unsupported operation or fields')
    if not is_owner(host):
        raise Rejected(OWNER_ONLY)
    return view(host)
