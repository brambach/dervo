"""Version switching against disposable workspaces. No provider or user app runs."""
import io
import json
from pathlib import Path
import plistlib
import tempfile
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
from concurrent.futures import Future
from types import SimpleNamespace
from host import Host
from dervo_connections import Rejected, Store
from release_handoff import file_hash, verified_backup
from release_switch import prepare, require_idle, verify_bundle, run, launch_environment, open_target

class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.host=Host(self.root/'data');self.addCleanup(self.close_host)
        self.host.store.add_profile('a','codex',self.root/'account','test@example.test','personal')
        (self.root/'project').mkdir();(self.root/'other').mkdir()
        self.host.store.add_project('p',self.root/'project','a','test-model')
        self.host.store.add_project('q',self.root/'other','a','test-model')
        self.current=self.bundle('old');self.target=self.bundle('new')
        self.host.previews=SimpleNamespace(read=lambda pid:{'state':'built','id':'new','directory':str(self.target.parent),'bundle':str(self.target)},tick=lambda:None,close=lambda:None,processes={})
        self.args={'projectId':'p','currentExecutable':str(self.current/'Contents/MacOS/dervo-local'),'dataOverride':True,'parentPid':1000000}
    def close_host(self):
        if self.host:
            self.host.close();self.host=None
    def bundle(self,name):
        bundle=self.root/name/'Dervo Local.app';resources=bundle/'Contents/Resources/runtime';resources.mkdir(parents=True)
        exe=bundle/'Contents/MacOS/dervo-local';exe.parent.mkdir();exe.write_text('#!/bin/sh\nexit 0\n');exe.chmod(0o700)
        (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.dervo.connected'}))
        (resources/'dervo-preview.json').write_text(json.dumps({'releaseId':name,'workspaceHandoff':1}))
        (bundle.parent/'release.json').write_text(json.dumps({'state':'built','bundle':str(bundle),'executable_sha256':file_hash(exe)}))
        return bundle
    def sign(self):return SwitchTests.teams(self, {})
    def test_native_plan_has_exact_scope_and_never_accepts_renderer_paths(self):
        plan=prepare(self.host,self.args)
        self.assertEqual(plan['workspace'],str(self.root/'data'))
        self.assertEqual(plan['target'],str(self.target))
        self.assertEqual(plan['current'],str(self.current))
        with self.assertRaises(Rejected):prepare(self.host,{**self.args,'command':'anything'})
        with self.assertRaises(Rejected):prepare(self.host,{**self.args,'projectId':'absent'})
        with self.assertRaises(Rejected):prepare(self.host,{**self.args,'dataOverride':'false'})
    def test_idle_check_covers_other_projects_and_background_checks(self):
        with patch('project_lifecycle.idle') as idle:
            require_idle(self.host)
            self.assertEqual([call.args[1] for call in idle.call_args_list],['p','q'])
        future=Future();self.host.checks['a']=future
        with self.assertRaisesRegex(Rejected,'account and model'):prepare(self.host,self.args)
        future.set_result({});self.host.checks.clear()
        self.host.logins['a']=SimpleNamespace(closed=False,profile={'provider':'claude'},cancel=lambda reason:None)
        with self.assertRaisesRegex(Rejected,'sign-in'):prepare(self.host,self.args)
        self.host.logins.clear()
        self.host.account_session=SimpleNamespace(operation={'id':'pending'},tick=lambda:None,close=lambda:None)
        with self.assertRaisesRegex(Rejected,'Dervo sign-in'):prepare(self.host,self.args)
    def test_bundle_requires_signature_marker_and_unchanged_executable(self):
        with self.sign():
            self.assertEqual(verify_bundle(self.target,release_id='new',handoff=True)['id'],'new')
            with self.assertRaisesRegex(RuntimeError,'doesn’t match'):verify_bundle(self.target,release_id='other')
            (self.target/'Contents/MacOS/dervo-local').write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'changed'):verify_bundle(self.target,release_id='new')
        with patch('release_switch.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr=b'')):
            with self.assertRaisesRegex(RuntimeError,'signature'):verify_bundle(self.current)
        marker=self.current/'Contents/Resources/runtime/dervo-preview.json';marker.write_text(json.dumps({'releaseId':'old'}))
        with self.assertRaisesRegex(RuntimeError,'Rebuild'):verify_bundle(self.current,handoff=True)

    def test_switch_keeps_lost_history_without_requiring_fake_completion(self):
        db = self.host.store.db
        self.host.coding_tasks()
        tasks = [dict(id=str(i), context={'project_id': 'q', 'thread_id': 'old'}, state=state,
                      runs=[{'state': 'uncertain'}], tokens=[{'state': 'uncertain'}])
                 for i, state in enumerate(('disconnected', 'needs_permission', 'answered'))]
        with db:
            for task in tasks:
                db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task['id'], json.dumps(task)))
        with patch.object(self.host, 'tick'):
            require_idle(self.host)
        self.assertEqual([json.loads(row[0]) for row in db.execute('SELECT document FROM coding_tasks ORDER BY id')], tasks)
        for token in ({'state': 'running'}, {'state': 'delivering'}, {'state': 'uncertain', 'job_id': 'job', 'job_done': False}):
            task = {**tasks[0], 'tokens': [token]}
            with db:
                db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(task), task['id']))
            with self.subTest(token=token), patch.object(self.host, 'tick'), self.assertRaisesRegex(Rejected, 'Stop the running work'):
                require_idle(self.host)
    def test_launch_keeps_default_and_explicit_store_modes(self):
        plan=prepare(self.host,self.args)
        self.assertEqual(launch_environment(plan)['DERVO_DATA_DIR'],str(self.root/'data'))
        plan['dataOverride']=False
        self.assertNotIn('DERVO_DATA_DIR',launch_environment(plan))
        self.assertNotIn('ANTHROPIC_API_KEY',launch_environment(plan))
    def test_each_version_finds_its_own_python(self):
        # 17 carries its own Python inside the app. Handing it over would run 16.4's host on 17's runtime (refused by its
        # keychain and confinement checks) or the next version on a copy inside an app that has just moved.
        plan=prepare(self.host,self.args)
        for data_override in (True,False):
            plan['dataOverride']=data_override
            self.assertEqual(set(launch_environment(plan))-{'DERVO_DATA_DIR'},{'HOME','PATH'})
    def test_download_switch_loads_what_it_needs_before_this_app_moves(self):
        import release_switch
        plan={**prepare(self.host,self.args),'channel':'download'};self.close_host()
        loaded={}
        def swap_in(current,staged,keep):
            loaded['release_selection']='release_selection' in sys.modules
            raise RuntimeError('stop after the move')
        sys.modules.pop('release_selection',None)
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report'),patch('release_switch.os.kill',side_effect=ProcessLookupError),patch('app_update.install_problem',return_value=None),patch('app_update.swap_in',side_effect=swap_in):
            with self.assertRaisesRegex(RuntimeError,'stop after the move'):release_switch.run(plan)
        self.assertEqual(loaded,{'release_selection':True})
    def test_live_workspace_prevents_backup_and_launch(self):
        plan=prepare(self.host,self.args)
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',return_value={'action':'backup'}),patch('release_switch.report'),patch('release_switch.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'still using'):run(plan)
        spawn.assert_not_called()
    def test_backup_verified_before_quit_permission_then_exact_launch(self):
        plan=prepare(self.host,self.args);self.close_host()
        reports=[]
        def report(value):
            reports.append(value['state'])
            if value['state']=='backed_up':
                verified_backup(Path(value['directory'])/'backup',self.root/'data')
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report',side_effect=report),patch('release_switch.os.kill',side_effect=ProcessLookupError),patch('release_switch.open_target',return_value=1234) as spawn:
            record=run(plan)
        self.assertEqual(reports,['prepared','backed_up'])
        self.assertEqual(record['state'],'opened')
        self.assertEqual(spawn.call_args.args[0]['executable'],str(self.target/'Contents/MacOS/dervo-local'))
        self.assertEqual(spawn.call_args.args[1]['DERVO_DATA_DIR'],str(self.root/'data'))
        self.assertTrue((Path(record['directory'])/'backup/workspace/conversations.sqlite3').exists())
        self.assertTrue(self.current.exists())
        self.assertTrue((Path(record['directory']).parent/'Open current Dervo.command').exists())
    def test_failed_backup_does_not_authorise_quit_or_launch(self):
        plan=prepare(self.host,self.args);self.close_host();reports=[]
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',return_value={'action':'backup'}),patch('release_switch.report',side_effect=reports.append),patch('release_switch.WorkspaceLease.snapshot',side_effect=OSError('disk full')),patch('release_switch.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(OSError,'disk full'):run(plan)
        self.assertEqual(reports,[{'state':'prepared'}]);spawn.assert_not_called()
        reopened=Store(self.root/'data/conversations.sqlite3');reopened.close()
    def test_cancel_after_backup_keeps_workspace_and_never_launches(self):
        plan=prepare(self.host,self.args);self.close_host()
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'cancel'}]),patch('release_switch.report'),patch('release_switch.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'cancelled'):run(plan)
        spawn.assert_not_called()
        reopened=Store(self.root/'data/conversations.sqlite3');reopened.close()
    def test_parent_that_does_not_exit_is_not_killed_or_overlapped(self):
        plan=prepare(self.host,self.args);self.close_host()
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report'),patch('release_switch.os.kill') as kill,patch('release_switch.time.monotonic',side_effect=[0,61]),patch('release_switch.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'still open'):run(plan)
        self.assertEqual(kill.call_args.args,(1000000,0));spawn.assert_not_called()

    def test_immediate_exit_and_failed_startup_never_publish_current(self):
        plan=prepare(self.host,self.args);self.close_host()
        with self.sign(),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.platform_os.app_support_dir',return_value=self.root/'Library/Application Support'),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report'),patch('release_switch.os.kill',side_effect=ProcessLookupError),patch('release_switch.open_target',side_effect=RuntimeError('stopped during startup')):
            with self.assertRaisesRegex(RuntimeError,'stopped during startup'):run(plan)
        records=list(self.root.glob('Library/Application Support/Dervo/Version history/*/*/switch.json'))
        self.assertEqual(len(records),1)
        self.assertEqual(json.loads(records[0].read_text())['state'],'failed')
        self.assertTrue((records[0].parent/'Open previous Dervo.command').exists())
        self.assertFalse((records[0].parent.parent/'Open current Dervo.command').exists())

    def startup_stub(self,body):
        script=self.root/'startup.py';script.write_text('#!'+sys.executable+'\n'+body);script.chmod(0o700)
        return {'executable':str(script)}

    def test_real_child_exit_before_acknowledgement_fails(self):
        target=self.startup_stub('import sys\nsys.exit(19)\n')
        with self.assertRaisesRegex(RuntimeError,'startup|startup connection'):
            open_target(target,{},self.root)

    def test_real_child_must_acknowledge_host_and_window(self):
        target=self.startup_stub("import os,json,time\nos.write(int(os.environ['DERVO_HANDOFF_FD']),json.dumps({'ready':True}).encode()+b'\\n')\ntime.sleep(.3)\n")
        self.assertIsInstance(open_target(target,{},self.root),int)
        target=self.startup_stub("import os,json,time\nos.write(int(os.environ['DERVO_HANDOFF_FD']),json.dumps({'ready':False,'detail':'fixture host failure'}).encode()+b'\\n')\ntime.sleep(.3)\n")
        with self.assertRaisesRegex(RuntimeError,'fixture host failure'):open_target(target,{},self.root)

    # --- Developer ID: the running app's Team ID gates every switch -------------------------------------------------
    def teams(self,mapping):
        """A fake codesign: --verify passes, -d prints the TeamIdentifier recorded for that bundle (None: ad hoc)."""
        def fake(command,**kwargs):
            if command[1]=='-d':
                team=mapping.get(command[-1])
                return SimpleNamespace(returncode=0,stderr=('Identifier=local.dervo.connected\n'+('Signature=adhoc\n' if team is None else '')+'TeamIdentifier='+(team or 'not set')+'\n').encode())
            return SimpleNamespace(returncode=0,stderr=b'')
        return patch('release_switch.subprocess.run',side_effect=fake)
    def test_signing_team_only_accepts_explicit_signed_or_ad_hoc_metadata(self):
        from release_switch import signing_team
        with self.teams({str(self.current): 'ABCDE12345'}):
            self.assertEqual(signing_team(self.current), 'ABCDE12345')
            self.assertIsNone(signing_team(self.target))
        for code, output in [(1, b'code object is not signed at all\n'),
                             (1, b'TeamIdentifier=ABCDE12345\n'),
                             (0, b''), (0, b'TeamIdentifier=../not-a-team\n'),
                             (0, b'TeamIdentifier=not set\n')]:
            with self.subTest(code=code, output=output), patch('release_switch.subprocess.run',
                    return_value=SimpleNamespace(returncode=code, stderr=output)):
                with self.assertRaisesRegex(RuntimeError, 'signing identity'):
                    signing_team(self.current)
    def switch_with_teams(self,mapping):
        plan=prepare(self.host,self.args);self.close_host()
        with self.teams(mapping),patch('release_switch.Path.home',return_value=self.root),patch('release_switch.receive',side_effect=[{'action':'backup'},{'action':'launch'}]),patch('release_switch.report'),patch('release_switch.os.kill',side_effect=ProcessLookupError),patch('release_switch.open_target',return_value=1234) as spawn:
            return run(plan),spawn
    def test_a_developer_id_app_switches_to_its_own_teams_bundle(self):
        record,spawn=self.switch_with_teams({str(self.current):'ABCDE12345',str(self.target):'ABCDE12345'})
        self.assertEqual(record['state'],'opened')
        self.assertEqual(spawn.call_args.args[0]['executable'],str(self.target/'Contents/MacOS/dervo-local'))
    def test_a_developer_id_app_refuses_another_teams_bundle_and_an_ad_hoc_one(self):
        for target_team in ('ZZZZZ99999',None):
            with self.subTest(target=target_team or 'ad hoc'):
                self.setUp()
                with self.assertRaisesRegex(RuntimeError,'isn’t signed by the developer'):
                    self.switch_with_teams({str(self.current):'ABCDE12345',str(self.target):target_team})
                self.assertEqual(list(self.root.glob('Library/Application Support/Dervo/Version history/*/*/backup')),[],'refused before any backup or launch')
    def test_an_ad_hoc_app_keeps_todays_checks_and_accepts_a_developer_id_update(self):
        # The control for the two above: no team on the running app, so a Developer ID bundle (the first signed
        # update every tester installs) and another ad hoc bundle both pass exactly as before.
        for target_team in ('ABCDE12345',None):
            with self.subTest(target=target_team or 'ad hoc'):
                self.setUp()
                record,spawn=self.switch_with_teams({str(self.current):None,str(self.target):target_team})
                self.assertEqual(record['state'],'opened')
    def test_verify_bundle_checks_the_team_only_when_asked(self):
        with self.teams({str(self.target):'ZZZZZ99999'}):
            self.assertEqual(verify_bundle(self.target,release_id='new',handoff=True)['id'],'new')
            self.assertEqual(verify_bundle(self.target,release_id='new',handoff=True,team='ZZZZZ99999')['id'],'new')
            with self.assertRaisesRegex(RuntimeError,'isn’t signed by the developer'):verify_bundle(self.target,release_id='new',handoff=True,team='ABCDE12345')
