from concurrent.futures import Future
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import beta_update_host as module
import platform_os
from beta_download import PendingFile
from beta_updates import SignedDownload
from dervo_connections import Rejected
from release_profiles import stage, manifest
from test_beta_updates import record, SESSION, PROJECT


class Executor:
    def submit(self, fn, *args):
        self.fn, self.args = fn, args
        self.future = Future(); self.future.set_running_or_notify_cancel()
        return self.future
    def finish(self):
        try: self.future.set_result(self.fn(*self.args))
        except Exception as e: self.future.set_exception(e)
    def shutdown(self, **kwargs): pass


class UpdateHost(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); stage(self.root, 'private-beta')
        (self.root/'runtime/dervo-preview.json').write_text(json.dumps({'releaseId':'trial.1',
            'releaseProfile':manifest('private-beta'),'nativeVersion':{'releaseId':'trial.1','productVersion':'0.1.0','buildNumber':'1'}}))
        self.account = Mock(); self.account.session = SESSION
        self.account.client.project = PROJECT
        self.account.client.clock.return_value = 1789460000
        self.identity = {'issuer':PROJECT.url+'/auth/v1','subject':SESSION.user_id}
        self.account.status.return_value = {'identity':self.identity}
        self.host = SimpleNamespace(account_session=self.account)
        # record() is a Mac row: on the Windows runner, ask for and fetch it as a Mac does.
        for patcher in (patch.object(module.beta_updates, 'this_platform', return_value='macos-arm64'),
                        patch.object(module, 'FETCH_FORMAT', 'zip')):
            patcher.start(); self.addCleanup(patcher.stop)
        self.executor = Executor()
        self.updates = module.Updates(self.host, resources=self.root, executor=self.executor)
        self.addCleanup(self.updates.close)

    def action(self, name, **args):
        return self.updates.dispatch(name, {'context':self.updates.status()['context'], **args})

    def available(self):
        with patch.object(module.beta_updates, 'latest', return_value={'state':'available','release':record()}):
            status = self.action('beta_update_check'); self.assertTrue(status['busy'])
            self.executor.finish()
        return self.updates.status()

    def start_download(self):
        return self.action('_beta_update_download', releaseId=record()['release_id'], folder=str(self.root))

    def output(self):
        return PendingFile(self.root, 'Dervo-trial.zip')

    def test_metadata_is_public_and_download_is_explicit(self):
        status = self.available()
        self.assertTrue(status['canDownload']); self.assertFalse(status['busy'])
        self.assertEqual(set(status['release']), {'release_id','title','notes','bytes','format'})
        text = json.dumps(status)
        self.assertNotIn(SESSION.access_token, text)
        self.assertNotIn('download_origin', text)
        self.assertNotIn('sha256', text)
        self.assertFalse(list(self.root.glob('*.zip')))

    def test_missing_native_identity_or_personal_profile_disables_updates(self):
        for personal in [False, True]:
            if personal: stage(self.root, 'personal-experimental')
            else: (self.root/'runtime/dervo-preview.json').unlink()
            self.assertFalse(self.updates.status()['available'])
            with self.assertRaises(Rejected): self.action('beta_update_check')

    def test_builder_profile_metadata_shows_version_before_release_check(self):
        status = self.updates.status()
        self.assertTrue(status['available'])
        self.assertTrue(status['canCheck'])
        self.assertEqual(status['current']['releaseId'], 'trial.1')
        self.assertEqual(status['state'], 'unchecked')
        self.assertFalse(status['canDownload'])

    def test_mismatched_or_malformed_preview_profile_disables_updates(self):
        path = self.root/'runtime/dervo-preview.json'
        data = json.loads(path.read_text())
        for value in ['private-beta', None, {}, manifest('personal-experimental'),
                      {**manifest('private-beta'), 'openRouter': True}]:
            with self.subTest(profile=value):
                data['releaseProfile'] = value
                path.write_text(json.dumps(data))
                self.assertFalse(self.updates.status()['available'])
                with self.assertRaises(Rejected): self.action('beta_update_check')

    def test_cancel_discards_completed_result_before_publication(self):
        self.available(); self.start_download()
        pending = self.output()
        self.executor.future.set_result(pending)
        self.updates.dispatch('beta_update_cancel', {'context':self.updates.context})
        self.assertTrue(pending.closed)
        self.assertFalse((self.root/'Dervo-trial.zip').exists())

    def test_same_account_reauthentication_discards_late_download(self):
        self.available(); self.start_download()
        pending = self.output(); old = self.updates.context
        self.updates.invalidate()
        self.executor.future.set_result(pending)
        self.updates.tick()
        self.assertTrue(pending.closed)
        with self.assertRaises(Rejected): self.updates.dispatch('beta_update_check', {'context':old})
        self.assertFalse((self.root/'Dervo-trial.zip').exists())

    def test_access_loss_clears_release_and_discards_pending_file(self):
        self.available(); self.start_download(); pending = self.output()
        self.executor.future.set_result(pending)
        self.account.require_access.side_effect = Rejected('expired')
        status = self.updates.status()
        self.assertFalse(status['canCheck']); self.assertIsNone(status['release'])
        self.assertTrue(pending.closed)

    def test_shutdown_cleans_completed_and_late_files_without_waiting(self):
        self.available(); self.start_download(); pending = self.output()
        self.updates.close(); self.executor.future.set_result(pending)
        self.assertTrue(pending.closed)
        self.assertFalse((self.root/'Dervo-trial.zip').exists())

    def test_native_path_and_selected_release_are_pinned(self):
        self.available()
        for args in [{'releaseId':'different','folder':str(self.root)},
                     {'releaseId':record()['release_id'],'folder':'relative'}]:
            with self.assertRaises(Rejected): self.action('_beta_update_download', **args)
        with self.assertRaises(Rejected): self.action('beta_update_check', url='https://evil.invalid')

    def download_checked_by(self, verifier):
        self.updates.verifier = verifier
        self.available(); self.start_download(); pending = self.output()
        with patch.object(module.beta_updates, 'download', return_value=SignedDownload('hidden',record(),1789460100)), \
             patch.object(module.beta_updates, '_access'), patch.object(module.beta_updates, '_rows', return_value=record()):
            self.executor.args = (*self.executor.args[:-2], Mock(return_value=pending), self.executor.args[-1])
            self.executor.finish()
        return pending, self.updates.status()

    def test_an_installer_the_release_key_didnt_sign_is_never_saved(self):
        refused = module.release_signature.REFUSED[False]
        verifier = Mock(side_effect=module.DownloadError(refused))
        pending, status = self.download_checked_by(verifier)
        self.assertEqual((status['state'], status['detail'], status['savedPath']), ('error', refused, None))
        self.assertTrue(pending.closed)
        self.assertFalse((self.root/'Dervo-trial.zip').exists())
        verifier.assert_called_once_with(self.root, record(), installing=False)

    def test_a_signed_installer_is_saved(self):
        # Negative control for the refusal above: the same download with a verifier that accepts it.
        pending, status = self.download_checked_by(Mock(return_value=None))
        self.assertEqual(status['state'], 'downloaded')
        self.assertTrue((self.root/'Dervo-trial.zip').exists())

    def test_worker_rechecks_remote_access_and_metadata_before_publish(self):
        self.available(); self.start_download(); pending = self.output()
        with patch.object(module.beta_updates, 'download', return_value=SignedDownload('hidden',record(),1789460100)), \
             patch.object(module.beta_updates, '_access'), patch.object(module.beta_updates, '_rows', return_value=None):
            self.updates.downloader = Mock(return_value=pending)
            # Arguments already captured at submission, replace the test seam only.
            self.executor.args = (*self.executor.args[:-2], self.updates.downloader, self.executor.args[-1])
            self.executor.finish()
        self.assertEqual(self.updates.status()['state'], 'error')
        self.assertTrue(pending.closed)

    def test_host_invalidates_updates_before_account_transition(self):
        from host import Host
        host = Host.__new__(Host); host.store = Mock(); host.account_session = Mock()
        host.store.db.execute.return_value.fetchone.return_value = None
        host.updates = Mock()
        def dispatch(*args):
            host.updates.invalidate.assert_called_once()
        host.account_session.dispatch.side_effect = dispatch
        Host.dispatch(host, 'dervo_account_sign_out', {})

    def test_worker_cleanup_failure_is_reported_without_private_details(self):
        import os
        self.available(); self.start_download()
        pending = self.output(); self.updates.progress['pending'] = pending
        with patch('beta_download.os.unlink', side_effect=PermissionError('private filesystem detail')):
            try:
                pending.discard()
            except PermissionError as error:
                self.executor.future.set_exception(error)
        status = self.updates.status()
        self.assertEqual(status['state'], 'error')
        self.assertTrue(pending.closed)
        self.assertTrue(pending.cleanup_failed)
        self.assertIn('partial installer', status['cleanupWarning'])
        self.assertNotIn('private filesystem detail', json.dumps(status))
        os.unlink(self.root / pending.temporary)

    def test_missing_or_unremovable_partial_never_blocks_signout_or_close(self):
        from host import Host
        import os
        self.available(); self.start_download()
        pending = self.output(); self.updates.progress['pending'] = pending
        platform_os.unlink_at(pending.temporary, pending.directory)
        self.updates.invalidate()
        self.assertTrue(pending.detached)
        pending.discard()
        broken = Mock(); broken.detach.side_effect = PermissionError('private filesystem detail')
        self.updates.progress['pending'] = broken
        host = Host.__new__(Host); host.store = Mock(); host.account_session = Mock()
        host.store.db.execute.return_value.fetchone.return_value = None
        host.updates = self.updates
        Host.dispatch(host, 'dervo_account_sign_out', {})
        host.account_session.dispatch.assert_called_once()
        self.assertNotIn('private filesystem detail', self.updates.cleanup_warning)
        self.updates.close()

    def start_fetch(self):
        updates_root = self.root / 'updates'
        patcher = patch('app_update.updates_root', return_value=updates_root)  # never the real Application Support
        patcher.start(); self.addCleanup(patcher.stop)
        return self.action('beta_update_fetch', releaseId=record()['release_id'])

    def test_a_running_download_says_which_kind_it_is(self):
        # Review, 23 Sep: canFetch is false for the whole of a download, so the card fell through to "Choose a folder for
        # the installer" during a normal Download update. The status now says which download it describes.
        self.assertIsNone(self.available()['mode'], 'nothing is downloading yet')
        status = self.start_fetch()
        self.assertEqual((status['state'], status['busy'], status['canFetch'], status['mode']), ('downloading', True, False, 'fetch'))
        self.updates.dispatch('beta_update_cancel', {'context': self.updates.context})
        self.assertIsNone(self.updates.status()['mode'], 'a cancelled download describes nothing')
        self.executor.future.set_exception(module.DownloadError('Download cancelled.'))
        self.updates.tick()
        self.available()
        status = self.start_download()
        self.assertEqual((status['state'], status['mode']), ('downloading', 'download'))
        self.executor.future.set_result(self.output())
        status = self.updates.status()
        self.assertEqual((status['state'], status['mode']), ('downloaded', 'download'))
        self.updates.invalidate()
        self.assertIsNone(self.updates.status()['mode'])

    def test_saving_the_installer_after_a_failed_update_never_unpacks_it(self):
        # Download update failed, then Save the installer instead: the saved installer stays an installer.
        self.available(); self.start_fetch()
        self.executor.future.set_exception(module.DownloadError('The download link expired. Try again.'))
        self.assertEqual(self.updates.status()['state'], 'error')
        self.start_download()
        self.executor.future.set_result(self.output())
        status = self.updates.status()
        self.assertEqual((status['state'], status['mode']), ('downloaded', 'download'))
        self.assertTrue((self.root/'Dervo-trial.zip').exists())

    def test_the_build_files_are_read_once_not_on_every_tick(self):
        # Efficiency review, 23 Sep: once Updates was opened, every host tick read and validated two JSON files.
        self.available()
        with patch.object(module, 'profile', wraps=module.profile) as profiles, \
                patch.object(module, 'running_version', wraps=module.running_version) as versions:
            for _ in range(50):
                self.updates.tick()
            self.assertEqual((profiles.call_count, versions.call_count), (0, 0), 'unchanged files are not read again')
            self.assertIsNotNone(self.updates.owner)
            # Access loss still counts at once: only the files are cached, never the account.
            self.account.require_access.side_effect = Rejected('expired')
            self.assertIsNone(self.updates.status()['release'])
            self.account.require_access.side_effect = None
            # A changed file is read again, and a build that stops being private-beta stops updates.
            stage(self.root, 'personal-experimental')
            import os
            os.utime(self.root/'runtime/dervo-release-profile.json', ns=(1, 1))
            self.updates.tick()
            self.assertGreaterEqual(profiles.call_count, 1)
            self.assertIsNone(self.updates.owner)
            self.assertFalse(self.updates.status()['canCheck'])


class StagingTeam(unittest.TestCase):
    """The Updates host hands the running app's Team ID to the stage check, and only when it runs from an .app."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def stage_with(self, resources):
        updates = module.Updates(SimpleNamespace(account_session=Mock()), resources=resources, executor=Executor())
        self.addCleanup(updates.close)
        seen = {}
        def fake_stage(archive, release, running, *, verify):
            seen['team'] = verify.keywords.get('team')
            return {'bundle': 'x', 'releaseId': release, 'build': 2}
        def fake_team(bundle):
            seen['asked'] = str(bundle)
            return 'ABCDE12345'
        # The Mac path: Windows and Linux stage an installer or package instead (windows_update, linux_update).
        with patch('app_update.stage', side_effect=fake_stage), patch('release_switch.signing_team', side_effect=fake_team), \
                patch.object(module.platform_os, 'WINDOWS', False), patch.object(module.platform_os, 'LINUX', False):
            updates._stage(self.root / 'Dervo-trial.zip', 'trial.2', 1)
        return seen

    def test_inside_an_app_the_running_apps_team_gates_the_download(self):
        resources = self.root / 'Dervo Local.app/Contents/Resources'; resources.mkdir(parents=True)
        seen = self.stage_with(resources)
        self.assertEqual(seen, {'asked': str(self.root / 'Dervo Local.app'), 'team': 'ABCDE12345'})

    def test_outside_an_app_no_team_is_asked_for_or_required(self):
        resources = self.root / 'checkout'; resources.mkdir()
        self.assertEqual(self.stage_with(resources), {'team': None})


class UpdatePrompt(unittest.TestCase):
    """The update prompt (beta.15, group 9) against a fake newer row in a local catalog: the real latest() read through
    the account client, over a fake HTTP opener. No hosted calls or writes."""
    def setUp(self):
        import sqlite3
        from supabase_client import SupabaseClient
        from test_beta_updates import ALLOWED, NOW
        from test_supabase_client import HTTP
        self.HTTP, self.ALLOWED, self.SupabaseClient, self.NOW = HTTP, ALLOWED, SupabaseClient, NOW
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); stage(self.root, 'private-beta')
        self.running('0.1.0-beta.15', 1789452916913000)
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.account = Mock(); self.account.session = SESSION
        self.account.status.return_value = {'identity':{'issuer':PROJECT.url+'/auth/v1','subject':SESSION.user_id}}
        self.host = SimpleNamespace(account_session=self.account, store=SimpleNamespace(db=self.db))
        # record() is a Mac row: on the Windows runner, ask for and fetch it as a Mac does.
        for patcher in (patch.object(module.beta_updates, 'this_platform', return_value='macos-arm64'),
                        patch.object(module, 'FETCH_FORMAT', 'zip')):
            patcher.start(); self.addCleanup(patcher.stop)
        self.now = [NOW]
        self.catalog()
        self.executor = Executor()
        self.updates = module.Updates(self.host, resources=self.root, executor=self.executor, clock=lambda: self.now[0])
        self.addCleanup(self.updates.close)

    def running(self, release_id, build):
        (self.root/'runtime/dervo-preview.json').write_text(json.dumps({'releaseId':release_id,
            'releaseProfile':manifest('private-beta'),'nativeVersion':{'releaseId':release_id,'productVersion':'0.1.0','buildNumber':str(build)}}))

    def catalog(self, *rows):
        """The account client, answering access and then each row in turn (an exception is a failed request)."""
        responses = []
        for row in rows:
            responses += [self.ALLOWED, row if isinstance(row, Exception) else [row]]
        self.http = self.HTTP(*responses)
        self.account.client = self.SupabaseClient(PROJECT, opener=self.http, clock=lambda: self.NOW)

    def newer(self, **changes):
        return record(**{'release_id':'0.1.0-beta.16', 'object_name':'0.1.0-beta.16/Dervo-macos-arm64.zip',
                         'build_number':1789452916914000, 'title':'Beta 16',
                         'notes':'- Pick the agent on Home\n- Icons in the Dock\n- Update prompt\n- A fourth line', **changes})

    def opened(self, *rows):
        """Open Dervo: the first notice starts the check, the catalog answers, the next notice shows the result."""
        self.catalog(*rows)
        first = self.updates.dispatch('beta_update_notice', {})
        self.assertEqual((first['prompt'], first['dot']), (None, False))
        self.assertEqual(first['status']['state'], 'checking')
        self.executor.finish()
        return self.updates.dispatch('beta_update_notice', {})

    def test_open_checks_the_catalog_once_and_prompts_for_a_newer_build(self):
        notice = self.opened(self.newer())
        self.assertEqual(notice['prompt'], {'releaseId':'0.1.0-beta.16', 'title':'Beta 16', 'bytes':300000000,
                                            'notes':['Pick the agent on Home', 'Icons in the Dock', 'Update prompt']})
        self.assertTrue(notice['dot'])
        self.assertTrue(notice['status']['canFetch'], 'Update now uses the same download as Settings > Updates')
        self.assertEqual([r.method for r in self.http.requests], ['POST', 'GET'])
        self.assertIn('order=build_number.desc&limit=1', self.http.requests[1].full_url)
        submitted = self.executor.future
        self.updates.dispatch('beta_update_notice', {})
        self.assertIs(self.executor.future, submitted, 'polling the notice doesn’t check again')
        self.assertNotIn(SESSION.access_token, json.dumps(notice))

    def test_later_hides_this_build_until_tomorrow(self):
        self.opened(self.newer())
        notice = self.updates.dispatch('beta_update_later', {'releaseId':'0.1.0-beta.16'})
        self.assertIsNone(notice['prompt'])
        self.assertTrue(notice['dot'], 'the dot stays until you’re on the newest build')
        self.now[0] += 3600
        self.assertIsNone(self.updates.dispatch('beta_update_notice', {})['prompt'])
        self.now[0] += 86400
        self.assertEqual(self.updates.dispatch('beta_update_notice', {})['prompt']['releaseId'], '0.1.0-beta.16')

    def test_later_doesnt_hide_the_next_build(self):
        self.opened(self.newer())
        self.updates.dispatch('beta_update_later', {'releaseId':'0.1.0-beta.16'})
        # A restart the same day, and beta.17 is out.
        self.updates = module.Updates(self.host, resources=self.root, executor=self.executor, clock=lambda: self.now[0])
        self.addCleanup(self.updates.close)
        notice = self.opened(self.newer(release_id='0.1.0-beta.17', object_name='0.1.0-beta.17/Dervo-macos-arm64.zip',
                                         build_number=1789452916915000))
        self.assertEqual(notice['prompt']['releaseId'], '0.1.0-beta.17')

    def test_offline_or_a_failed_check_says_nothing_on_open_and_settings_shows_the_error(self):
        from urllib.error import URLError
        notice = self.opened(URLError('offline'))
        self.assertEqual((notice['prompt'], notice['dot'], notice['updated']), (None, False, None))
        status = self.updates.dispatch('beta_update_status', {})
        self.assertEqual(status['state'], 'error')
        self.assertTrue(status['detail'])

    def test_on_the_newest_build_there_is_no_prompt_and_no_dot(self):
        notice = self.opened(self.newer(build_number=1789452916913000, release_id='0.1.0-beta.15',
                                        object_name='0.1.0-beta.15/Dervo-macos-arm64.zip'))
        self.assertEqual((notice['prompt'], notice['dot']), (None, False))
        self.assertEqual(notice['status']['state'], 'current')

    def test_it_checks_again_every_few_hours(self):
        self.opened(self.newer(build_number=1789452916913000, release_id='0.1.0-beta.15',
                               object_name='0.1.0-beta.15/Dervo-macos-arm64.zip'))
        submitted = self.executor.future
        self.now[0] += module.AUTO_SECONDS - 1
        self.updates.dispatch('beta_update_notice', {})
        self.assertIs(self.executor.future, submitted)
        self.now[0] += 1
        self.catalog(self.newer())
        self.assertEqual(self.updates.dispatch('beta_update_notice', {})['status']['state'], 'checking')
        self.executor.finish()
        self.assertEqual(self.updates.dispatch('beta_update_notice', {})['prompt']['releaseId'], '0.1.0-beta.16')

    def test_no_check_without_beta_access_or_on_a_personal_build(self):
        self.account.require_access.side_effect = Rejected('expired')
        self.catalog()
        notice = self.updates.dispatch('beta_update_notice', {})
        self.assertEqual((notice['prompt'], notice['dot']), (None, False))
        self.assertFalse(hasattr(self.executor, 'fn'))
        self.account.require_access.side_effect = None
        stage(self.root, 'personal-experimental')
        self.assertEqual(self.updates.dispatch('beta_update_notice', {}), {'prompt':None,'dot':False,'updated':None,'status':None})
        self.assertFalse(hasattr(self.executor, 'fn'))

    def test_a_running_download_is_never_replaced_by_a_check(self):
        self.opened(self.newer())
        # As Update now leaves it while the installer comes down.
        self.updates.state = 'downloading'; self.updates.future = Future()
        self.now[0] += module.AUTO_SECONDS * 2
        notice = self.updates.dispatch('beta_update_notice', {})
        self.assertEqual(notice['status']['state'], 'downloading')
        self.assertEqual(notice['prompt']['releaseId'], '0.1.0-beta.16', 'the prompt stays up to show progress')

    def test_after_an_update_the_first_open_says_so_once_with_what_changed(self):
        store = module.NoticeStore(self.host)
        store.write(seen='0.1.0-beta.14')
        store.remember_notes('0.1.0-beta.15', '- Pick an icon\n- Update prompt')
        notice = self.opened(self.newer(build_number=1789452916913000, release_id='0.1.0-beta.15',
                                        object_name='0.1.0-beta.15/Dervo-macos-arm64.zip'))
        self.assertEqual(notice['updated'], {'releaseId':'0.1.0-beta.15', 'notes':['Pick an icon', 'Update prompt']})
        with self.assertRaises(Rejected):
            self.updates.dispatch('beta_update_seen', {'releaseId':'0.1.0-beta.14'})
        self.assertIsNone(self.updates.dispatch('beta_update_seen', {'releaseId':'0.1.0-beta.15'})['updated'])
        again = module.Updates(self.host, resources=self.root, executor=Executor(), clock=lambda: self.now[0])
        self.addCleanup(again.close)
        self.assertIsNone(again.dispatch('beta_update_notice', {})['updated'], 'once, across restarts')

    def test_the_first_open_with_this_record_announces_only_for_an_existing_workspace(self):
        self.catalog()
        self.assertIsNone(self.updates.dispatch('beta_update_notice', {})['updated'], 'a new tester isn’t told about an update')
        self.assertEqual(module.NoticeStore(self.host).read()['seen'], '0.1.0-beta.15')
        self.db.execute('delete from update_notice')
        self.db.execute('create table projects (id text)'); self.db.execute("insert into projects values ('p')")
        self.assertEqual(self.updates.dispatch('beta_update_notice', {})['updated'], {'releaseId':'0.1.0-beta.15', 'notes':[]})

    def test_fetch_remembers_the_notes_for_the_next_open(self):
        self.opened(self.newer())
        with patch.object(module, 'app_update', create=True):
            import app_update
            with patch.object(app_update, 'updates_root', return_value=self.root/'staging'):
                self.updates.dispatch('beta_update_fetch', {'context':self.updates.context, 'releaseId':'0.1.0-beta.16'})
        self.assertEqual(module.NoticeStore(self.host).read()['notes']['releaseId'], '0.1.0-beta.16')

    def test_notice_fields_are_exact(self):
        for operation, args in [('beta_update_notice', {'x':1}), ('beta_update_later', {}), ('beta_update_seen', {'releaseId':1}),
                                ('beta_update_later', {'releaseId':''})]:
            with self.assertRaises(Rejected):
                self.updates.dispatch(operation, args)
