"""Main-thread update state with background metadata checks and installer reads.

The update prompt (beta.15, group 9) reads ``beta_update_notice``: on open, and again every few hours while Dervo runs,
it checks the catalog the same way Check for updates does, and says whether to show the prompt, the dot on Settings
and, once after an update, "You're on <version>". A failed check says nothing there; Settings > Updates shows the
error. Nothing downloads or installs without the person's click, and there's no silent auto-update.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import beta_updates
import platform_os
from beta_download import fetch_installer, DownloadError, PendingFile
from dervo_connections import Rejected
from release_profiles import read as profile, MARKER
import release_signature
from release_version import validate

FIELDS = {'beta_update_status': set(), 'beta_update_check': {'context'}, 'beta_update_cancel': {'context'},
          'beta_update_fetch': {'context', 'releaseId'}, '_beta_update_download': {'context', 'releaseId', 'folder'},
          # The update prompt (beta.15, group 9): what to show on open, Later for one build until tomorrow, and the
          # one-time "You're on" message after an update.
          'beta_update_notice': set(), 'beta_update_later': {'releaseId'}, 'beta_update_seen': {'releaseId'}}
# How often an open Dervo checks the catalog again by itself.
AUTO_SECONDS = 4 * 3600
# What Download update fetches and stages for Update and restart: a zipped app on a Mac, the installer on Windows
# (windows_update), and on Linux the format Dervo was installed as (linux_update). A Mac DMG is only saved with
# Download installer.
FETCH_FORMAT = 'exe' if platform_os.WINDOWS else beta_updates.linux_format() if platform_os.LINUX else 'zip'
# The prompt shows this many lines of a release's notes.
NOTICE_LINES = 3
# States an automatic check may replace. It never interrupts a download or a ready update, and a newer build already
# offered stays offered (a re-check would blank the prompt while it runs).
AUTO_FROM = ('unchecked', 'current', 'unpublished', 'error', 'cancelled')


def running_version(resources):
    path = resources / 'runtime/dervo-preview.json'
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError()
    data = json.loads(path.read_text())
    version = data['nativeVersion']
    validate(version['productVersion'], version['releaseId'], version['buildNumber'])
    bundled_profile = profile(resources)
    if (version['releaseId'] != data['releaseId']
            or bundled_profile['profile'] != 'private-beta'
            or data['releaseProfile'] != bundled_profile):
        raise ValueError()
    return version


class Updates:
    def __init__(self, host, *, resources=None, executor=None, downloader=fetch_installer, verifier=release_signature.check,
                 clock=time.time):
        self.host = host
        self.clock = clock
        # When the last automatic check started, and the prompt's small record on this Mac (NoticeStore).
        self.auto_at = None
        self.notices = NoticeStore(host)
        self.resources = Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
        self.executor, self.downloader, self.verifier = executor, downloader, verifier
        self.future = None
        self.cancelled = Event()
        self.closed = False
        self.context = uuid4().hex
        self.owner = None
        self.state, self.detail = 'unchecked', 'Check for a newer Dervo beta.'
        self.release = self.saved_path = None
        self.progress = {'received': 0}
        self.cleanup_warning = ''
        # A newer Dervo ready for Update and restart: a local build beside this app, or a downloaded and unpacked one.
        self.update = None
        self.fetching = None
        # Which download the card describes: 'fetch' (Download update, unpacked to switch to) or 'download' (the installer
        # saved to a folder you chose). Set when either starts, so the copy follows it (review, 23 Sep).
        self.mode = None
        # This build's profile and version, checked again only when their files change (_check_build).
        self.build_key = self.build_error = self.build_paths = None

    def _detach_pending(self):
        pending = self.progress.get('pending')
        if pending:
            try:
                pending.detach()
            except Exception:
                self.cleanup_warning = 'A partial installer couldn’t be removed. Check the download folder.'

    def _discard(self, pending):
        try:
            pending.discard()
        except Exception:
            self.cleanup_warning = 'A partial installer couldn’t be removed. Check the download folder.'

    def _build_identity(self):
        """What the build check read, as stat results: the resources folder, runtime/ and the two files."""
        if self.build_paths is None:
            runtime = self.resources / 'runtime'
            self.build_paths = tuple(map(str, (self.resources, runtime, runtime / MARKER, runtime / 'dervo-preview.json')))
        identity = []
        for path in self.build_paths:
            try:
                found = os.lstat(path)
                identity.append((found.st_mode, found.st_ino, found.st_size, found.st_mtime_ns, found.st_ctime_ns))
            except OSError:
                identity.append(None)
        return tuple(identity)

    def _check_build(self):
        """Raises unless this is a private-beta build with a readable version. Every host tick asks (through _owner) once
        Settings > Updates has been opened, and reading and validating the two JSON files each time cost 0.6% of a core
        for the rest of the session (efficiency review, 23 Sep). Now they're read again only when a stat changes."""
        identity = self._build_identity()
        if identity != self.build_key:
            self.build_key, self.build_error = identity, None
            try:
                if profile(self.resources)['profile'] != 'private-beta':
                    raise Rejected('Beta updates aren’t available in this build.')
                running_version(self.resources)
            except Exception as error:  # noqa: BLE001 - kept and raised as it was, on every call until the files change
                self.build_error = error
        if self.build_error is not None:
            raise self.build_error.with_traceback(None)

    def _owner(self):
        if self.closed:
            raise Rejected('Beta updates aren’t available in this build.')
        self._check_build()
        account = self.host.account_session
        account.require_access()
        session = account.session
        identity = account.status().get('identity')
        if (not session or not identity or identity['subject'] != session.user_id
                or identity['issuer'] != account.client.project.url + '/auth/v1'):
            raise Rejected('Check your Dervo account before downloading an update.')
        account.require_identity(identity['issuer'], identity['subject'])
        return account, identity['issuer'], identity['subject']

    def invalidate(self):
        self.cancelled.set()
        self._detach_pending()
        self.context = uuid4().hex
        self.owner = self.release = self.saved_path = None
        self.state, self.detail = 'unchecked', 'Check for a newer Dervo beta.'
        self.progress = {'received': 0}
        self.update = self.fetching = self.mode = None
        self.auto_at = None

    def _verify(self, *, installing):
        resources, verifier = self.resources, self.verifier
        return lambda release: verifier(resources, release, installing=installing)

    def _bundle(self):
        bundle = self.resources.parent.parent
        return bundle if bundle.suffix == '.app' else None

    def _local(self):
        import app_update
        bundle = self._bundle()
        try:
            return app_update.local_candidate(bundle) if bundle else None
        except OSError:
            return None

    def _ready(self, update):
        self.update = dict(update)
        self.state = 'ready'
        if update.get('source') == 'package':
            self.detail = (f"{update['releaseId']} is downloaded and checked. Update and restart backs up your workspace, closes "
                           'Dervo and opens the package in your software installer. Open Dervo again once it’s installed.')
            return
        self.detail = (f"{update['releaseId']} is ready. Update and restart backs up your workspace, closes Dervo and opens the new "
                       'version. One earlier copy is kept so you can go back.')

    def _stage(self, archive, release, running, row=None):
        if platform_os.WINDOWS:
            import windows_update
            return {'staged': windows_update.stage(archive, row or {}, running)}
        if platform_os.LINUX:
            import linux_update
            return {'staged': linux_update.stage(archive, row or {}, running, FETCH_FORMAT)}
        import app_update
        from functools import partial
        from release_switch import verify_bundle, signing_team
        # A Developer ID app only stages a download its own team signed. The switch checks this again (release_switch.run).
        bundle = self._bundle()
        team = signing_team(bundle) if bundle else None
        try:
            return {'staged': app_update.stage(archive, release, running, verify=partial(verify_bundle, team=team))}
        except Exception:
            try:
                Path(archive).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _sync(self):
        try:
            owner = self._owner()
        except Exception:
            owner = None
        if owner != self.owner:
            self.invalidate()
            self.owner = owner
        return owner

    def tick(self):
        if self.closed:
            return
        owner = self._sync()
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        result = None
        try:
            result = future.result()
            if self.cancelled.is_set() or not owner or owner != self._owner():
                if isinstance(result, PendingFile):
                    self._discard(result)
                return
            if isinstance(result, PendingFile):
                try:
                    published = result.publish()
                    if result.cleanup_failed:
                        self.cleanup_warning = 'A partial installer couldn’t be removed. Check the download folder.'
                finally:
                    self._discard(result)
                if self.fetching:
                    # Checked download: unpack it off the main thread, then it's ready to switch to.
                    self.state, self.detail = 'unpacking', 'Download checked. Unpacking the new version…'
                    self.future = self.executor.submit(self._stage, published, self.fetching,
                                                       int(running_version(self.resources)['buildNumber']),
                                                       dict(self.release) if self.release else None)
                    return
                self.saved_path = published
                self.state, self.detail = 'downloaded', 'Installer saved and checksum verified. Open it when you’re ready to install.'
            elif isinstance(result, dict) and 'staged' in result:
                self.fetching = None
                staged = result['staged']
                # A Linux .deb or .rpm is installed by the desktop's software installer (linux_update).
                self._ready({**staged, 'source': 'package' if staged.get('format') in ('deb', 'rpm') else 'download'})
            else:
                self.state, self.release = result['state'], result['release']
                self.detail = {'unpublished': 'No beta release has been published yet.',
                               'current': 'You’re using the latest published beta.',
                               'available': 'A newer Dervo beta is ready to download.'}[self.state]
        except Exception as error:
            if not self.cancelled.is_set():
                self.state = 'error'
                self.detail = str(error) if isinstance(error, DownloadError) else 'Dervo couldn’t check or download this update. Check your account and connection, then retry.'
        finally:
            if isinstance(result, PendingFile):
                self._discard(result)
            pending = self.progress.get('pending')
            if isinstance(pending, PendingFile) and pending.cleanup_failed:
                self.cleanup_warning = 'A partial installer couldn’t be removed. Check the download folder.'

    def status(self):
        self.tick()
        try:
            version = running_version(self.resources)
            available = profile(self.resources)['profile'] == 'private-beta'
        except Exception:
            version, available = None, False
        public = {key: self.release[key] for key in ('release_id', 'title', 'notes', 'bytes', 'format')} if self.release else None
        update = {'source': self.update['source'], 'releaseId': self.update['releaseId']} if self.update else None
        return {'available': available, 'current': version, 'context': self.context, 'update': update,
                'canUpdate': bool(self.update and self.state == 'ready' and not self.future and not self.closed),
                'canFetch': bool(self.owner and not self.future and self.release and self.release.get('format') == FETCH_FORMAT
                                 and self.state in ('available', 'error', 'cancelled')),
                'state': self.state, 'detail': self.detail, 'release': public,
                'busy': self.future is not None, 'canCheck': bool(self.owner and not self.future and not self.closed),
                'canDownload': bool(self.owner and not self.future and self.release and self.state in ('available', 'error', 'cancelled')),
                'received': self.progress['received'], 'savedPath': self.saved_path, 'cleanupWarning': self.cleanup_warning,
                'mode': self.mode if self.state in ('downloading', 'unpacking', 'downloaded') else None,
                'installProblem': self.install_problem()}

    def install_problem(self):
        """Why this app couldn't be replaced by an update where it runs now, or None (also None outside an app)."""
        from app_update import MOVE_FIRST, install_problem, running_bundle, updates_root
        if platform_os.LINUX and FETCH_FORMAT == 'appimage':
            import linux_update
            try:
                line = linux_update.appimage_problem(platform_os.app_executable(self.resources))
            except RuntimeError:
                return None  # Not run as an app (tests, a source checkout).
            return {'kind': 'permission', 'line': line} if line else None
        bundle = running_bundle(self.resources)
        if bundle is None:
            return None
        # Downloads unpack under Updates/, so compare with that disk once it exists.
        root = updates_root()
        line = install_problem(bundle, root / 'Dervo Local.app' if root.is_dir() else None)
        # 'move': running from Downloads, a disk image or another disk, so the prompt offers the move on open.
        # 'permission': already in Applications without write access, only said when an update is ready.
        return {'kind': 'move' if line == MOVE_FIRST else 'permission', 'line': line} if line else None

    @staticmethod
    def _download(client, session, resources, release, folder, cancelled, progress, downloader, verify):
        if cancelled.is_set():
            raise DownloadError('Download cancelled.')
        link = beta_updates.download(client, session, resources, release['release_id'])
        # An explicit click approves the displayed artifact, not later metadata.
        if link.release != release:
            raise DownloadError('This release changed. Check for updates again before downloading.')
        if client.clock() >= link.expires_at:
            raise DownloadError('The download link expired. Try again.')
        output = downloader(link, folder, cancelled, progress)
        try:
            # Recheck membership and release visibility before exposing the file.
            beta_updates._access(client, session, resources)
            current = beta_updates._rows(client, session, '&release_id=eq.' + release['release_id'] + '&limit=1')
            if current != release or cancelled.is_set():
                raise DownloadError('This download is no longer available. Check for updates again.')
            # The installer's bytes matched this release's SHA-256 above. Its signature says the release key published
            # that SHA-256 for this release id, build and version. Nothing is saved or unpacked without both.
            verify(release)
            return output
        except Exception:
            output.discard()
            raise

    def dispatch(self, operation, args):
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported beta update fields.')
        # Cancellation wins over an already finished background download.
        if operation == 'beta_update_cancel':
            self._sync()
            if args['context'] != self.context:
                raise Rejected('These update controls are no longer current.')
            self.cancelled.set()
            self._detach_pending()
            self.context = uuid4().hex
            self.mode = None
            self.state, self.detail = 'cancelled', 'Update check or download cancelled.'
            return self.status()
        self.tick()
        if operation == 'beta_update_status':
            return self.status()
        if operation == 'beta_update_notice':
            return self.notice()
        if operation in ('beta_update_later', 'beta_update_seen'):
            return self._answer_notice(operation, args['releaseId'])
        if operation == 'beta_update_check' and args['context'] == self.context and self.future is None and not self.closed:
            # A newer build beside this app needs no account or network.
            local = self._local()
            if local:
                self.release = None
                self._ready({**local, 'source': 'local'})
                return self.status()
        if args['context'] != self.context or not self.owner or self.future is not None or self.closed:
            raise Rejected('Check your Dervo account and wait for the current update operation to finish.')
        account = self._owner()[0]
        client, session = account.client, account.session
        self.executor = self.executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-update')
        self.cancelled = Event()
        self.progress = {'received': 0}
        self.saved_path = None
        if operation == 'beta_update_check':
            self.release = self.update = self.mode = None
            self.state, self.detail = 'checking', 'Checking for a newer beta…'
            self.future = self.executor.submit(beta_updates.latest, client, session, self.resources,
                                               int(running_version(self.resources)['buildNumber']))
        elif operation == '_beta_update_download':
            if not self.release or self.release['release_id'] != args['releaseId'] or self.state not in ('available', 'error', 'cancelled'):
                raise Rejected('Check for updates before choosing an installer.')
            if type(args['folder']) is not str or not Path(args['folder']).is_absolute():
                raise Rejected('Choose a download folder in the native picker.')
            # An earlier Download update that failed leaves ``fetching`` set, and tick would unpack this installer as one.
            self.fetching, self.mode = None, 'download'
            self.state, self.detail = 'downloading', 'Downloading and checking the installer…'
            self.future = self.executor.submit(self._download, client, session, self.resources, dict(self.release),
                        args['folder'], self.cancelled, self.progress, self.downloader, self._verify(installing=False))
        elif operation == 'beta_update_fetch':
            if not self.release or self.release['release_id'] != args['releaseId'] or self.state not in ('available', 'error', 'cancelled'):
                raise Rejected('Check for updates before downloading.')
            if self.release.get('format') != FETCH_FORMAT:
                raise Rejected('This release is a disk image. Download the installer and open it instead.')
            import app_update
            folder = app_update.updates_root() / self.release['release_id']
            if folder.is_symlink():
                raise Rejected('The update folder needs review before downloading.')
            if folder.exists():
                import shutil
                shutil.rmtree(folder)  # Dervo's own staging folder: an earlier partial attempt at this release.
            folder.mkdir(parents=True, mode=0o700)
            self.fetching = self.release['release_id']
            self.update, self.mode = None, 'fetch'
            # So the first open after this update can say what changed (notice).
            self.notices.remember_notes(self.release['release_id'], self.release['notes'])
            self.state, self.detail = 'downloading', 'Downloading and checking the new version…'
            self.future = self.executor.submit(self._download, client, session, self.resources, dict(self.release),
                        str(folder), self.cancelled, self.progress, self.downloader, self._verify(installing=True))
        return self.status()

    # ------------------------------------------------------------------ the update prompt (beta.15, group 9)
    def _auto_check(self):
        """Check the catalog on open and every AUTO_SECONDS after, as Check for updates would, minus the local-build
        shortcut. Only for a signed-in person with beta access, and never over a running download or a ready update."""
        if self.closed or self.future is not None or not self.owner or self.state not in AUTO_FROM:
            return
        if self.auto_at is not None and self.clock() - self.auto_at < AUTO_SECONDS:
            return
        try:
            build = int(running_version(self.resources)['buildNumber'])
            account = self._owner()[0]
        except Exception:  # noqa: BLE001 - not a private-beta build, or the account changed: nothing to check
            return
        self.auto_at = self.clock()
        self.executor = self.executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-update')
        self.cancelled = Event()
        self.progress = {'received': 0}
        self.release = self.update = self.mode = self.saved_path = None
        self.state, self.detail = 'checking', 'Checking for a newer beta…'
        self.future = self.executor.submit(beta_updates.latest, account.client, account.session, self.resources, build)

    def _newer(self, current):
        """The newer build this person could move to, as (release id, release or None), or None."""
        if self.update and self.state == 'ready':
            return self.update['releaseId'], self.release
        if self.release and self.state in ('available', 'downloading', 'unpacking', 'downloaded', 'cancelled', 'error'):
            if self.release['build_number'] > int(current['buildNumber']):
                return self.release['release_id'], self.release
        return None

    def _today(self):
        return time.strftime('%Y-%m-%d', time.localtime(self.clock()))

    def notice(self):
        """What the renderer shows on open: ``prompt`` (a newer build, unless Later hid it today), ``dot`` (on Settings
        and Updates until this is the newest build) and ``updated`` (this version, once, after an update)."""
        self.tick()
        empty = {'prompt': None, 'dot': False, 'updated': None, 'status': None}
        if self.closed:
            return empty
        try:
            current = running_version(self.resources)
        except Exception:  # noqa: BLE001 - a development or personal build has no beta updates
            return empty
        self._auto_check()
        record = self.notices.read()
        updated = None
        if record['seen'] is None:
            # The first open with this record. Someone who already had projects came from an older build; a new
            # tester's first open isn't an update.
            if self.notices.existing_workspace():
                updated = current['releaseId']
            else:
                self.notices.write(seen=current['releaseId'])
        elif record['seen'] != current['releaseId']:
            updated = current['releaseId']
        newer = self._newer(current)
        prompt = None
        if newer:
            later = record['later']
            hidden = bool(later and later.get('releaseId') == newer[0] and later.get('day') == self._today())
            release = newer[1] if newer[1] and newer[1]['release_id'] == newer[0] else None
            if not hidden:
                prompt = {'releaseId': newer[0], 'title': release['title'] if release else newer[0],
                          'notes': notice_lines(release['notes']) if release else [],
                          'bytes': release['bytes'] if release else None}
        notes = record['notes']
        return {'prompt': prompt, 'dot': bool(newer), 'status': self.status(),
                'updated': {'releaseId': updated,
                            'notes': notice_lines(notes['notes']) if notes and notes.get('releaseId') == updated else []}
                if updated else None}

    def _answer_notice(self, operation, release_id):
        if type(release_id) is not str or not release_id or len(release_id) > 80:
            raise Rejected('Unsupported beta update fields.')
        if operation == 'beta_update_later':
            # Later hides this build's prompt until tomorrow. A newer build shows again at once.
            self.notices.write(later={'releaseId': release_id, 'day': self._today()})
        else:
            try:
                current = running_version(self.resources)['releaseId']
            except Exception:  # noqa: BLE001
                raise Rejected('Beta updates aren’t available in this build.') from None
            if release_id != current:
                raise Rejected('That isn’t the version you’re using.')
            self.notices.write(seen=current, notes=None)
        return self.notice()

    def close(self):
        self.closed = True
        self.invalidate()
        if self.future:
            def discard(future):
                try:
                    result = future.result()
                    if isinstance(result, PendingFile):
                        result.discard()
                except Exception:
                    pass
            self.future.add_done_callback(discard)
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)


def notice_lines(text):
    """The first few lines of release notes, without list markers."""
    lines = [line.strip().lstrip('-*•').strip() for line in str(text or '').split('\n')]
    return [line for line in lines if line][:NOTICE_LINES]


class NoticeStore:
    """The update prompt's record on this Mac, in the host's database: the last version this person saw announced
    (``seen``), the build they said Later to and on which day (``later``), and the notes of the update they downloaded
    (``notes``), so the first open after it can say what changed. A host without a store (tests) keeps it in memory."""
    EMPTY = {'seen': None, 'later': None, 'notes': None}

    def __init__(self, host):
        self.host = host
        self.db = getattr(getattr(host, 'store', None), 'db', None)
        self.memory = dict(self.EMPTY)
        if self.db is not None:
            with self.db:
                self.db.execute('create table if not exists update_notice (id integer primary key check(id=1), document text not null)')

    def read(self):
        if self.db is None:
            return dict(self.memory)
        row = self.db.execute('select document from update_notice where id=1').fetchone()
        try:
            saved = json.loads(row[0]) if row else {}
            if type(saved) is not dict:
                raise ValueError()
        except ValueError:
            saved = {}
        record = dict(self.EMPTY)
        record.update({key: saved[key] for key in self.EMPTY if key in saved})
        return record

    def write(self, **changes):
        record = self.read()
        record.update(changes)
        if self.db is None:
            self.memory = record
            return
        with self.db:
            self.db.execute('insert into update_notice values (1,?) on conflict(id) do update set document=excluded.document',
                            (json.dumps(record),))

    def remember_notes(self, release_id, notes):
        self.write(notes={'releaseId': release_id, 'notes': str(notes or '')[:4000]})

    def existing_workspace(self):
        if self.db is None:
            return False
        try:
            return self.db.execute('select 1 from projects limit 1').fetchone() is not None
        except Exception:  # noqa: BLE001 - no projects table yet is a new workspace
            return False
