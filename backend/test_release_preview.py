"""Preview setup uses disposable source and stores; process calls are doubles."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from host import Host
from dervo_connections import Rejected
from release_builder import snapshot,validate_cache
from release_storage import BuildStorage
from release_preview import running_release

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'project';self.source.mkdir()
        (self.source/'tauri.conf.json').write_text(json.dumps({'identifier':'local.dervo.connected'}))
        self.host=Host(self.root/'data');self.addCleanup(self.host.close)
        self.host.store.add_profile('account','codex',self.root/'account','test@example.test','personal')
        self.host.store.add_project('p',self.source,'account','test-model')
    def call(self,op,**args):return self.host.dispatch(op,{'projectId':'p',**args})
    def test_status_never_starts_build_and_rejects_non_dervo(self):
        with patch('release_preview.subprocess.Popen') as spawn:
            self.assertTrue(self.call('preview_status')['supported']);spawn.assert_not_called()
        (self.source/'tauri.conf.json').write_text('{}')
        with self.assertRaises(Rejected):self.call('preview_start',tools={})

    def test_saved_version_limit_blocks_start_and_folder_action_uses_host_path(self):
        releases=self.root/'Dervo Releases'
        for n in range(8):(releases/str(n)/'Dervo Local.app').mkdir(parents=True)
        with patch('release_preview.Path.home',return_value=self.root),patch('release_preview.subprocess.Popen') as spawn:
            report=self.call('preview_status')['storage']
            self.assertEqual(report['retainedApps'],8)
            self.assertFalse(report['canBuild'])
            with self.assertRaisesRegex(Rejected,'Review saved versions'):
                self.call('preview_start',tools={})
            spawn.assert_not_called()
            self.assertFalse((self.host.root/'preview-tools.json').exists())
            self.call('preview_storage_folder')
            spawn.assert_called_once_with(['/usr/bin/open',str(releases)])
            with self.assertRaises(Rejected):self.call('preview_storage_folder',path='/somewhere-else')

    def test_status_retires_legacy_cache_import_without_rewriting_settings(self):
        path=self.host.root/'preview-tools.json'
        original={'cacheRoot':str(self.root/'deleted-old-target'),'node':'/usr/local/bin/node'}
        path.write_text(json.dumps(original))
        with patch('release_preview.subprocess.Popen') as spawn:
            self.assertEqual(self.call('preview_status')['tools'],{'node':'/usr/local/bin/node'})
            spawn.assert_not_called()
        self.assertEqual(json.loads(path.read_text()),original)
        with self.assertRaises(Rejected):self.call('preview_start',tools={'cacheRoot':str(self.source)})

    def test_running_release_comes_from_host_marker_not_latest_job(self):
        resources=self.root/'Resources';(resources/'runtime').mkdir(parents=True)
        marker=resources/'runtime/dervo-preview.json'
        self.assertIsNone(running_release(resources))
        marker.write_text(json.dumps({'releaseId':'installed-1','source':'not exposed'}))
        self.assertEqual(running_release(resources),{'id':'installed-1'})
        self.call('preview_status')
        self.host.previews.jobs['p']={'id':'candidate-2','directory':str(self.root/'candidate'),'state':'built'}
        with patch('release_preview.running_release',return_value=running_release(resources)):
            status=self.call('preview_status')
        self.assertEqual(status['running_release']['id'],'installed-1')
        self.assertEqual(status['job']['id'],'candidate-2')
        for invalid in ['bad json','[]',json.dumps({'releaseId':'bad\nidentifier'})]:
            marker.write_text(invalid);self.assertIsNone(running_release(resources))
    def test_start_has_fixed_command_snapshot_output_and_explicit_tools(self):
        process=MagicMock();process.poll.return_value=None;process.pid=123
        with patch('release_preview.os.killpg') as kill,patch('release_preview.Path.home',return_value=self.root),patch('release_preview.subprocess.Popen',return_value=process) as spawn:
            job=self.call('preview_start',tools={})
            command=spawn.call_args.args[0]
            self.assertIn('--source',command);self.assertIn(str(self.source),command)
            self.assertTrue(Path(job['directory']).is_relative_to(self.root/'Dervo Releases'))
            self.assertNotIn('shell',spawn.call_args.kwargs)
            self.assertNotIn('ANTHROPIC_API_KEY',spawn.call_args.kwargs['env'])
            with self.assertRaises(Rejected):self.call('preview_start',tools={})
            with self.assertRaisesRegex(Rejected,'preview build'):self.call('project_archive')
            # Delete stops a build itself now (app #12), but only once the confirmation names this project: a refused
            # delete stops nothing.
            with self.assertRaisesRegex(Rejected,'exact project'):self.call('project_delete',confirmation='DELETE')
            self.assertIn('p',self.host.previews.processes)
            process.poll.return_value=1
            self.assertEqual(self.call('preview_status')['job']['state'],'failed')
            self.assertEqual(kill.call_count,2)
            self.assertNotIn('p',self.host.previews.processes)
        for tools in [{'command':'evil'},{'node':'relative'},{'node':True}]:
            with self.assertRaises((Rejected,TypeError)):self.call('preview_start',tools=tools)
    def test_open_uses_retained_app_and_separate_data_without_second_launch(self):
        self.call('preview_status');folder=self.root/'release';bundle=folder/'Dervo Local.app';bundle.mkdir(parents=True)
        self.host.previews.jobs['p']={'id':'test','directory':str(folder),'state':'built','bundle':str(bundle)}
        process=MagicMock();process.pid=123;process.poll.return_value=None
        with patch('release_preview.subprocess.Popen',return_value=process) as spawn:
            self.call('preview_open');self.call('preview_open')
            self.assertEqual(spawn.call_count,1)
            self.assertEqual(spawn.call_args.kwargs['env']['DERVO_DATA_DIR'],str(folder/'preview-data'))
            self.assertNotEqual(spawn.call_args.kwargs['env']['DERVO_DATA_DIR'],str(self.host.root))
            second=self.root/'second';(second/'Dervo Local.app').mkdir(parents=True)
            self.host.previews.jobs['p']={'id':'second','directory':str(second),'state':'built','bundle':str(second/'Dervo Local.app')}
            self.call('preview_open')
            self.assertEqual(spawn.call_count,2)
    def test_snapshot_excludes_local_data_and_refuses_source_links(self):
        (self.source/'frontend/node_modules').mkdir(parents=True)
        (self.source/'backend').mkdir();(self.source/'backend/host.py').write_text('source')
        (self.source/'frontend/.env.local').write_text('private')
        (self.source/'backend/linked.py').symlink_to(self.root/'outside')
        with self.assertRaisesRegex(RuntimeError,'Linked source'):snapshot(self.source,self.root/'bad')
        (self.source/'backend/linked.py').unlink()
        result=snapshot(self.source,self.root/'snapshot')
        self.assertNotIn('frontend/.env.local',result)
        self.assertEqual((self.root/'snapshot/backend/host.py').read_text(),'source')
        (self.root/'snapshot/backend/host.py').write_text('copy changed')
        self.assertEqual((self.source/'backend/host.py').read_text(),'source')

    def test_schema_ancestor_and_cache_links_are_rejected(self):
        (self.source/'frontend/node_modules').mkdir(parents=True)
        external=self.root/'external';(external/'codex-app-server').mkdir(parents=True)
        (self.source/'schemas').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'Linked protocol schema'):
            snapshot(self.source,self.root/'snapshot')
        cache=self.root/'cache';cache.mkdir()
        (cache/'release').symlink_to(external,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'Linked build cache entry'):
            validate_cache(cache)
        self.assertTrue((external/'codex-app-server').exists())

    def test_cancel_kills_descendants_even_if_leader_exited(self):
        self.call('preview_status')
        process=MagicMock();process.pid=123;process.poll.return_value=1
        self.host.previews.processes['p']=process
        with patch('release_preview.os.killpg') as kill:
            self.host.previews.stop('p')
        self.assertEqual(kill.call_count,2)
        self.assertNotIn('p',self.host.previews.processes)

    def test_low_space_does_not_spawn_or_save_a_new_build(self):
        with patch('release_preview.require_space',side_effect=RuntimeError('This preview needs 6 GB free.')),patch('release_preview.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(Rejected,'6 GB free'):
                self.call('preview_start',tools={})
        spawn.assert_not_called()
        self.assertEqual(self.host.previews.jobs,{})

    def test_cancel_cleans_owned_staging_but_keeps_cache_and_evidence(self):
        self.call('preview_status')
        with patch('release_storage.Path.home',return_value=self.root):
            storage=BuildStorage(self.source)
            with storage.acquire():
                storage.work.mkdir();(storage.work/'leftover').write_text('temporary')
                storage.target.mkdir();(storage.target/'keep').write_text('compiler cache')
            folder=self.root/'release';folder.mkdir();(folder/'check.log').write_text('failure evidence')
            self.host.previews.jobs['p']={'id':'test','directory':str(folder),'state':'building','storage_version':1,'source':str(self.source)}
            result=self.call('preview_cancel')
            self.assertEqual(result['state'],'cancelled')
            self.assertFalse(storage.work.exists())
            self.assertTrue((storage.target/'keep').exists())
            self.assertTrue((folder/'check.log').exists())

    def test_cancel_never_cleans_another_builders_locked_staging(self):
        self.call('preview_status')
        with patch('release_storage.Path.home',return_value=self.root):
            storage=BuildStorage(self.source)
            with storage.acquire():
                storage.work.mkdir();(storage.work/'keep').write_text('active source')
                self.host.previews.jobs['p']={'id':'test','directory':str(self.root/'release'),'state':'building','storage_version':1,'source':str(self.source)}
                result=self.call('preview_cancel')
                self.assertTrue((storage.work/'keep').exists())
                self.assertIn('next build',result['storage_note'])


class BundledResourcesInSnapshotTests(unittest.TestCase):
    """Every top-level file tauri.conf.json bundles must be in the build's source snapshot, or cargo's build script
    stops with "resource path ... doesn't exist" (beta.11-preview.2, LICENSE.md)."""

    def top_level_files(self, conf):
        resources = json.loads(conf.read_text())['bundle']['resources']
        return [item for item in resources if '/' not in item and '*' not in item]

    def test_every_bundled_top_level_file_is_snapshotted(self):
        import release_builder
        conf = Path(__file__).resolve().parent.parent / 'tauri.conf.json'
        files = self.top_level_files(conf)
        self.assertIn('LICENSE.md', files)
        self.assertEqual([name for name in files if name not in release_builder.SOURCE_ITEMS], [])

    def test_negative_control_a_missing_entry_is_caught(self):
        import release_builder
        conf = Path(__file__).resolve().parent.parent / 'tauri.conf.json'
        items = [name for name in release_builder.SOURCE_ITEMS if name != 'LICENSE.md']
        self.assertEqual([name for name in self.top_level_files(conf) if name not in items], ['LICENSE.md'])

    def test_the_root_files_feature_checks_read_are_snapshotted(self):
        # beta.18's first build: test_codex_runtime read both from the snapshot and the build stopped at feature-checks.
        import release_builder
        for name in ('tauri.windows.conf.json', '.gitignore'):
            self.assertIn(name, release_builder.SOURCE_ITEMS)

    def test_every_folder_the_frontend_imports_from_outside_itself_is_snapshotted(self):
        # 9 Oct, the Mac build: frontend/src/local/phoneLink.ts imports ../../../companion, which the snapshot left out,
        # so frontend-types failed (TS2307) after the feature checks had passed.
        import re
        import release_builder
        root = Path(__file__).resolve().parent.parent
        frontend = root / 'frontend'
        outside = set()
        for path in (frontend / 'src').rglob('*'):
            if path.suffix not in ('.ts', '.tsx') or 'node_modules' in path.parts:
                continue
            for relative in re.findall(r"""from\s+['"]((?:\.\./)+[^'"]+)['"]""", path.read_text(errors='ignore')):
                target = (path.parent / relative).resolve()
                try:
                    parts = target.relative_to(root).parts
                except ValueError:
                    continue
                if parts and parts[0] != 'frontend':
                    outside.add(parts[0])
        self.assertIn('companion', outside, 'the scan should find the phone link')
        self.assertEqual(sorted(name for name in outside if name not in release_builder.SOURCE_ITEMS), [])

class BuildCommitsTests(unittest.TestCase):
    """A build records the commits its source HEAD holds (release_builder.source_commits), so Bugs and ideas can say
    "Fixed in <release>". A fake source tree, no build."""
    def test_source_commits_lists_the_head_history_newest_first_and_nothing_for_a_folder_without_git(self):
        import subprocess
        from release_builder import source_commits
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'src';root.mkdir()
        def git(*args):
            done=subprocess.run(['/usr/bin/git','-C',str(root),*args],capture_output=True,text=True,env={'PATH':'/usr/bin:/bin','HOME':temp.name,'GIT_TERMINAL_PROMPT':'0'})
            self.assertEqual(done.returncode,0,done.stderr);return done.stdout.strip()
        self.assertEqual(source_commits(root),[],'not a repository yet')
        git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.test')
        self.assertEqual(source_commits(root),[],'a repository with no commit')
        (root/'a').write_text('1');git('add','-A');git('commit','-q','-m','one');first=git('rev-parse','HEAD')
        (root/'a').write_text('2');git('add','-A');git('commit','-q','-m','two');second=git('rev-parse','HEAD')
        self.assertEqual(source_commits(root),[second,first])
        self.assertEqual(source_commits(root,limit=1),[second],'the cap keeps the newest')
        self.assertEqual(source_commits(Path(temp.name)/'missing'),[])
