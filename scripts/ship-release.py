#!/usr/bin/env python3
"""Ship one Dervo release to every platform and every place people hear about it, from one command on Bryce's Mac.

    ship-release.py prepare BASE
    ship-release.py collect BASE --mac MAC_PACKAGE --out FOLDER [--run-windows ID] [--run-linux ID]
    ship-release.py sign FOLDER
    ship-release.py publish FOLDER --notes NOTES.md [--channel private-beta|admin-preview] [--go]
    ship-release.py announce FOLDER --notes NOTES.md [--go]

BASE is the Mac release id, like 0.1.0-beta.19. The other platforms carry it with their own ending: 0.1.0-beta.19-windows
for the Windows installer and 0.1.0-beta.19-linux-deb, -linux-rpm and -linux-appimage for the Linux packages, each a
catalog row of its own (supabase/migrations/202609260001 and 202610080001).

1. prepare writes .github/windows-release.json and .github/linux-release.json. Commit and push them to mvp1: the Windows
   and Linux build workflows then build release packages for BASE (scripts/windows-release.py, linux-release.py).
2. collect downloads those packages from the runs with `gh run download` and copies the Mac package (from
   backend/release_package.py) beside them, one folder per catalog row, and checks every package.json.
3. sign runs tools/release-sign on each folder. 1Password asks for Touch ID; the key never leaves it.
4. publish puts each signed installer in the R2 bucket the app downloads from and adds its catalog row, which is the
   update queue: every installed Dervo on that platform and format sees it in Settings > Updates and the update prompt.
5. announce tells people: the Discord releases channel, the website's /beta section (a manifest the site reads and the
   installers it serves), a GitHub release in brambach/dervo with the installers attached, and CHANGELOG.md.

publish and announce only print what they would do until --go is given, and refuse unsigned packages. Nothing here
runs on its own: Bryce runs it. The settings come from the environment, never argv, and none is printed:

    DERVO_R2_ACCOUNT_ID             the Cloudflare account id (32 hex), for the catalog's download_origin
    DERVO_R2_BUCKET                 the bucket the app downloads from (dervo-beta-releases)
    DERVO_CATALOG_DATABASE_URL      a Postgres connection string for the Supabase project, for the catalog rows
    DERVO_DISCORD_RELEASES_WEBHOOK  the Discord webhook URL for the releases channel
    DERVO_SITE_BUCKET               the bucket trydervo.com serves /beta from (dervo-beta-installers)
    wrangler and gh signed in on this Mac (wrangler login, gh auth login)
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPO = 'brambach/dervo'
SITE = 'https://trydervo.com/beta'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}')
# Each catalog row: its folder in the collected release, platform, format, id ending and the name testers see.
ROWS = (('macos', 'macos-arm64', None, '', 'Mac (Apple silicon)'),
        ('windows', 'windows-x64', 'exe', '-windows', 'Windows'),
        ('linux-deb', 'linux-x64', 'deb', '-linux-deb', 'Linux (.deb: Ubuntu, Debian)'),
        ('linux-rpm', 'linux-x64', 'rpm', '-linux-rpm', 'Linux (.rpm: Fedora, openSUSE)'),
        ('linux-appimage', 'linux-x64', 'appimage', '-linux-appimage', 'Linux (AppImage)'))
WORKFLOWS = {'windows': ('windows.yml', 'dervo-windows-release-{base}-windows'),
             'linux': ('linux.yml', 'dervo-linux-release-{base}-linux')}


class ShipError(RuntimeError):
    pass


def fail(message):
    raise ShipError(message)


def base_id(base):
    if type(base) is not str or not ID.fullmatch(base) or base.endswith(('-windows', '-linux')) or '-linux-' in base:
        fail('BASE is the Mac release id, like 0.1.0-beta.19, without a platform ending.')
    return base


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


# 1. prepare

def prepare(root, base):
    base = base_id(base)
    written = {}
    for name, value in (('windows-release.json', base + '-windows'), ('linux-release.json', base + '-linux')):
        path = root / '.github' / name
        path.write_text(json.dumps({'releaseId': value}) + '\n')
        written[str(path.relative_to(root))] = value
    return {'written': written, 'next': 'Commit these two files and push them to mvp1. The Windows and Linux build '
                                        'workflows build the release packages; then run collect with their run ids.'}


# 2. collect

def read_package(folder, platform, fmt, release_id):
    """One package folder's package.json, checked against its installer and the catalog row it becomes."""
    package = json.loads((folder / 'package.json').read_text())
    artifact = folder / package['artifact']
    if package['releaseId'] != release_id:
        fail(f'{folder} holds {package["releaseId"]}, not {release_id}.')
    if '/' in package['artifact'] or not artifact.is_file() or artifact.is_symlink():
        fail(f'{folder} has no installer named {package["artifact"]}.')
    if digest(artifact) != package['sha256'] or artifact.stat().st_size != package['bytes']:
        fail(f'{artifact} doesn\'t match its package.json.')
    if fmt is None:
        fmt = {'.zip': 'zip', '.dmg': 'dmg'}.get(artifact.suffix)
        if fmt is None:
            fail(f'The Mac package {artifact.name} is neither a ZIP nor a DMG.')
    native = package['bundleAudit']['nativeVersion']
    if native['releaseId'] != release_id:
        fail(f'{folder}\'s app is stamped {native["releaseId"]}, not {release_id}.')
    signature = None
    signed = folder / 'release-signature.json'
    if signed.is_file():
        record = json.loads(signed.read_text())
        if (record.get('releaseId'), record.get('sha256')) != (release_id, package['sha256']):
            fail(f'{signed} signs something else.')
        signature = record['signature']
    return {'release_id': release_id, 'platform': platform, 'format': fmt, 'artifact': str(artifact),
            'name': package['artifact'], 'sha256': package['sha256'], 'bytes': package['bytes'],
            'build_number': int(native['buildNumber']), 'product_version': native['productVersion'],
            'object_name': f'{release_id}/Dervo-{platform}.{fmt}', 'signature': signature}


def release_rows(folder, base):
    rows = []
    for name, platform, fmt, ending, label in ROWS:
        row = read_package(folder / name, platform, fmt, base + ending)
        rows.append({**row, 'folder': str(folder / name), 'label': label})
    versions = {row['product_version'] for row in rows}
    if len(versions) != 1:
        fail('The packages are different product versions: ' + ', '.join(sorted(versions)))
    builds = [(row['platform'], row['format'], row['build_number']) for row in rows]
    if len({build for _, _, build in builds}) != len(builds):
        fail('Two packages share a build number; each catalog row needs its own.')
    return rows


def collect(base, mac, out, runs, run=subprocess.run):
    base = base_id(base)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(mac, out / 'macos', symlinks=False)
    with tempfile.TemporaryDirectory() as scratch:
        for platform, (workflow, artifact) in WORKFLOWS.items():
            target = Path(scratch) / platform
            argv = ['gh', 'run', 'download', '--repo', REPO, '--name', artifact.format(base=base), '--dir', str(target)]
            if runs.get(platform):
                argv[3:3] = [str(runs[platform])]
            done = run(argv, capture_output=True, text=True)
            if done.returncode:
                fail(f'Couldn\'t download {artifact.format(base=base)} ({workflow}): {done.stderr.strip()[:500]}')
            if platform == 'windows':
                shutil.copytree(target, out / 'windows')
            else:
                for fmt in ('deb', 'rpm', 'appimage'):
                    shutil.copytree(target / fmt, out / ('linux-' + fmt))
    return [{key: row[key] for key in ('release_id', 'platform', 'format', 'bytes', 'build_number')}
            for row in release_rows(out, base)]


# 3. sign

def sign(folder, run=subprocess.run):
    signed = []
    for name, *_ in ROWS:
        package = Path(folder) / name
        if (package / 'release-signature.json').exists():
            signed.append(name + ' (already signed)')
            continue
        done = run(['cargo', 'run', '--quiet', '--manifest-path', str(ROOT / 'tools/release-sign/Cargo.toml'), '--',
                    'sign', str(package.resolve())])
        if done.returncode:
            fail(f'Signing {package} failed. Nothing after it was signed.')
        signed.append(name)
    return signed


# 4. publish

def notes_of(path):
    text = Path(path).read_text().strip()
    lines = text.splitlines()
    if not lines:
        fail('The notes file is empty.')
    title = lines[0].lstrip('# ').strip()
    body = '\n'.join(lines[1:]).strip() or title
    if not 1 <= len(title) <= 120 or len(body) > 4000:
        fail('The notes need a first-line title up to 120 characters and at most 4,000 characters after it.')
    return title, body


def sql_text(value):
    return "'" + str(value).replace("'", "''") + "'"


def catalog_sql(rows, title, notes, channel, origin):
    if channel not in ('private-beta', 'admin-preview'):
        fail('The channel is private-beta or admin-preview.')
    if not re.fullmatch(r'https://[a-f0-9]{32}\.r2\.cloudflarestorage\.com', origin):
        fail('DERVO_R2_ACCOUNT_ID must be the 32-character Cloudflare account id.')
    values = []
    for row in rows:
        if row['signature'] is None:
            fail(f'{row["release_id"]} isn\'t signed. Run sign first: an unsigned row is never installed.')
        values.append('(' + ', '.join([
            sql_text(row['release_id']), str(row['build_number']), sql_text(row['product_version']), sql_text(channel),
            sql_text(row['platform']), sql_text(title), sql_text(notes), sql_text(row['format']),
            sql_text(row['object_name']), sql_text(origin), sql_text(row['sha256']), str(row['bytes']),
            sql_text(row['signature']), 'now()']) + ')')
    return ('begin;\ninsert into public.dervo_beta_releases\n (release_id, build_number, product_version, channel, '
            'platform, title, notes, format, object_name, download_origin, sha256, bytes, signature, published_at)\n'
            'values\n ' + ',\n '.join(values) + ';\ncommit;\n')


def publish(folder, base, notes, channel, go, env=os.environ, run=subprocess.run):
    rows = release_rows(Path(folder), base)
    title, body = notes_of(notes)
    bucket = env.get('DERVO_R2_BUCKET', 'dervo-beta-releases')
    origin = 'https://' + env.get('DERVO_R2_ACCOUNT_ID', '') + '.r2.cloudflarestorage.com'
    sql = catalog_sql(rows, title, body, channel, origin)
    uploads = [['npx', '--yes', 'wrangler', 'r2', 'object', 'put', f'{bucket}/{row["object_name"]}', '--file',
                row['artifact'], '--content-type', 'application/octet-stream', '--remote'] for row in rows]
    (Path(folder) / 'catalog.sql').write_text(sql)
    plan = {'uploads': [u[6] for u in uploads], 'catalogRows': [r['release_id'] for r in rows], 'channel': channel,
            'sql': str(Path(folder) / 'catalog.sql')}
    if not go:
        return {'dryRun': True, **plan}
    database = env.get('DERVO_CATALOG_DATABASE_URL')
    if not database:
        fail('Set DERVO_CATALOG_DATABASE_URL to add the catalog rows. Nothing was uploaded.')
    for argv in uploads:
        if run(argv).returncode:
            fail(f'Uploading {argv[6]} failed. No catalog row was added, so no app sees this release yet.')
    # Rows go in last and together, so an app never sees a release whose installer isn't there yet.
    done = run(['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', str(Path(folder) / 'catalog.sql')],
               env={**env, 'PGDATABASE': database})
    if done.returncode:
        fail('Adding the catalog rows failed; none was added (one transaction). The uploads are in place.')
    return {'dryRun': False, **plan}


# 5. announce

def size(n):
    return f'{n / 1024 / 1024:.0f} MB'


def discord_message(base, title, body, rows):
    lines = [f'{row["label"]}: {size(row["bytes"])}' for row in rows]
    return {'username': 'Dervo', 'allowed_mentions': {'parse': []},
            'content': f'**Dervo {base}** is out on Mac, Windows and Linux.',
            'embeds': [{'title': title[:256], 'url': SITE, 'description': body[:3500],
                        'fields': [{'name': 'Downloads', 'value': '\n'.join(lines)[:1024]},
                                   {'name': 'Updating', 'value': 'Dervo offers it in Settings > Updates, or download '
                                                                 f'it from {SITE}.'}]}]}


def site_manifest(base, title, body, rows, now):
    return {'schema': 1, 'releaseId': base, 'version': rows[0]['product_version'], 'title': title, 'notes': body,
            'publishedAt': now, 'downloads': [{'platform': r['platform'], 'format': r['format'], 'label': r['label'],
                                               'file': f'{base}/{Path(r["artifact"]).name}', 'bytes': r['bytes'],
                                               'sha256': r['sha256']} for r in rows]}


def changelog_entry(base, title, body, day):
    return f'## {base} ({day})\n\n**{title}**\n\n{body}\n\n'


def post_json(url, payload):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'dervo-ship-release'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def announce(folder, base, notes, go, root=ROOT, env=os.environ, run=subprocess.run, post=post_json, now=None):
    folder = Path(folder)
    rows = release_rows(folder, base)
    if any(row['signature'] is None for row in rows):
        fail('Announce after publish: every package must be signed and in the catalog first.')
    title, body = notes_of(notes)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    manifest = site_manifest(base, title, body, rows, now.isoformat(timespec='seconds'))
    (folder / 'beta.json').write_text(json.dumps(manifest, indent=2) + '\n')
    site = env.get('DERVO_SITE_BUCKET', 'dervo-beta-installers')
    steps = {
        'discord': 'post the release to the Discord webhook in DERVO_DISCORD_RELEASES_WEBHOOK',
        'site': [f'{site}/{d["file"]}' for d in manifest['downloads']] + [f'{site}/beta/latest.json'],
        'github': f'gh release create {base} --repo {REPO} with {len(rows)} installers',
        'changelog': str(root / 'CHANGELOG.md'),
    }
    if not go:
        return {'dryRun': True, 'steps': steps, 'discordMessage': discord_message(base, title, body, rows)}
    webhook = env.get('DERVO_DISCORD_RELEASES_WEBHOOK', '')
    if not webhook.startswith('https://discord.com/api/webhooks/'):
        fail('Set DERVO_DISCORD_RELEASES_WEBHOOK to the releases channel\'s webhook. Nothing was announced.')
    done = []
    for row in rows:
        target = f'{site}/{base}/{Path(row["artifact"]).name}'
        if run(['npx', '--yes', 'wrangler', 'r2', 'object', 'put', target, '--file', row['artifact'], '--remote']).returncode:
            fail(f'Uploading {target} for the website failed. Announced so far: {done or "nothing"}.')
    if run(['npx', '--yes', 'wrangler', 'r2', 'object', 'put', f'{site}/beta/latest.json', '--file',
            str(folder / 'beta.json'), '--content-type', 'application/json', '--remote']).returncode:
        fail(f'Uploading the /beta manifest failed. Announced so far: {done or "nothing"}.')
    done.append('site')
    notes_file = folder / 'release-notes.md'
    notes_file.write_text(f'{title}\n\n{body}\n')
    if run(['gh', 'release', 'create', base, '--repo', REPO, '--title', f'Dervo {base}', '--notes-file',
            str(notes_file), *[row['artifact'] for row in rows]]).returncode:
        fail(f'Creating the GitHub release failed. Announced so far: {done}.')
    done.append('github')
    changelog = root / 'CHANGELOG.md'
    existing = changelog.read_text() if changelog.exists() else '# Dervo changelog\n\n'
    head, _, rest = existing.partition('\n\n')
    changelog.write_text(head + '\n\n' + changelog_entry(base, title, body, now.date().isoformat()) + rest)
    done.append('changelog')
    # Discord last, so the message only goes out when the downloads it points to are there.
    post(webhook, discord_message(base, title, body, rows))
    done.append('discord')
    return {'dryRun': False, 'announced': done}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare').add_argument('base')
    c = sub.add_parser('collect')
    c.add_argument('base')
    c.add_argument('--mac', required=True)
    c.add_argument('--out', required=True)
    c.add_argument('--run-windows')
    c.add_argument('--run-linux')
    sub.add_parser('sign').add_argument('folder')
    for name in ('publish', 'announce'):
        p = sub.add_parser(name)
        p.add_argument('folder')
        p.add_argument('--notes', required=True)
        p.add_argument('--go', action='store_true')
        if name == 'publish':
            p.add_argument('--channel', default='private-beta')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(ROOT, args.base)
        elif args.command == 'collect':
            result = collect(args.base, args.mac, args.out, {'windows': args.run_windows, 'linux': args.run_linux})
        elif args.command == 'sign':
            result = sign(args.folder)
        else:
            base = json.loads((Path(args.folder) / 'macos' / 'package.json').read_text())['releaseId']
            if args.command == 'publish':
                result = publish(args.folder, base, args.notes, args.channel, args.go)
            else:
                result = announce(args.folder, base, args.notes, args.go)
    except (ShipError, OSError, ValueError, KeyError) as exc:
        print('ship-release.py: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
