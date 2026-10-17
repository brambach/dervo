"""Disposable project-folder grant and confined check regressions."""
import os, re, shutil, time, unittest
from pathlib import Path
from dervo_connections import Rejected
import project_access
from test_task_evidence import TaskEvidenceTests as EvidenceFixture
from test_conversation_coding import ConversationCodingTests as ConversationFixture

TITLEBAR='titlebar.test.mjs'
TITLEBAR_INPUT=re.compile(r"""new URL\(\s*['"]\./([^'"]+)['"]""")   # the literal project files the real check reads

class ProjectAccessTests(unittest.TestCase):
    setUp = EvidenceFixture.setUp
    service = EvidenceFixture.service
    def create(self, project=True, test_file='test_sample.py', profile=None):
        service=self.service()
        task=service.create(files=[test_file],test_file=test_file,project_access=project,check_profile=profile,**self.ctx)
        service.approve(task['id'],scope_version=1,**self.ctx)
        return service,task
    def test_source_access_and_creation(self):
        (self.root/'src').mkdir(); (self.root/'src/main.py').write_text('value = 1\n')
        service,task=self.create()
        self.assertIn('src',str(service.list_files(task['id'],'.',**self.ctx)))
        self.assertIn('value',str(service.search_files(task['id'],'.','value',**self.ctx)))
        before=service.read_file(task['id'],'src/main.py',**self.ctx)
        attempt=service.prepare(task['id'],changes={'src/main.py':{'expected_sha256':before['sha256'],'content':b'value = 2\n'}},**self.ctx)
        self.assertEqual(attempt['state'],'prepared')
        service.create_file(task['id'],'README.md',b'Documentation\n',**self.ctx)
        service.create_file(task['id'],'src/new.py',b'value = 3\n',**self.ctx)
        self.assertEqual((self.root/'README.md').read_text(),'Documentation\n')
    def test_legacy_and_exclusions(self):
        (self.root/'normal.py').write_text('value = 1')
        service,legacy=self.create(False)
        with self.assertRaises(Rejected): service.list_files(legacy['id'],'.',**self.ctx)
        with self.assertRaises(Rejected): service.read_file(legacy['id'],'normal.py',**self.ctx)
        service,task=self.create()
        for name in ['.env','secret.txt','token.json','client.key','state.sqlite','node_modules/x.js','../outside.py','/tmp/foo']:
            with self.subTest(name=name), self.assertRaises(Rejected): service.read_file(task['id'],name,**self.ctx)
        (self.root/'linked.py').symlink_to(self.root/'normal.py'); os.link(self.root/'normal.py',self.root/'hard.py')
        for name in ['linked.py','hard.py']:
            with self.assertRaises(Rejected): service.read_file(task['id'],name,**self.ctx)
        with self.assertRaises(Rejected): service.create_file(task['id'],'.env',b'private',**self.ctx)
    def test_ordinary_names_like_data_and_tokens_are_readable_and_a_refusal_names_its_rule(self):
        # 16.2 (Bryce, 27 Sep): data.js was refused as "hidden, secret or generated" because its first word was "data".
        for name in ['data.js','docs/data-model.md','backend/auth_flow.py','src/tokens.css','frontend/src/profile.tsx']:
            (self.root/name).parent.mkdir(parents=True,exist_ok=True); (self.root/name).write_text('x = 1\n')
        (self.root/'secrets').mkdir(); (self.root/'secrets/prod.json').write_text('{}')
        service,task=self.create()
        for name in ['data.js','docs/data-model.md','backend/auth_flow.py','src/tokens.css','frontend/src/profile.tsx']:
            with self.subTest(name=name): self.assertEqual(service.read_file(task['id'],name,**self.ctx)['bytes_b64'],'eCA9IDEK')  # "x = 1\n"
        listed=str(service.list_files(task['id'],'.',**self.ctx))
        self.assertIn('data.js',listed); self.assertNotIn('secrets',listed)
        for name,why in [('.env','starts with a dot'),('secrets/prod.json','secrets folder'),('state.sqlite','database'),('client.key','private key')]:
            with self.subTest(name=name), self.assertRaisesRegex(Rejected,why): service.read_file(task['id'],name,**self.ctx)
    def test_root_replacement(self):
        service,task=self.create()
        self.root.rename(self.root.with_name('previous')); self.root.mkdir()
        (self.root/'test_sample.py').write_text('replacement')
        with self.assertRaisesRegex(Rejected,'replaced'): service.read_file(task['id'],'test_sample.py',**self.ctx)
    def test_actual_titlebar_inputs_run_automatically_and_expire(self):
        source=Path(__file__).resolve().parent.parent
        # Read the inputs out of the real check file instead of pinning them. The list grew (src/main.rs,
        # frontend/src/main.tsx) and the pinned copy went stale, which made collection look broken when it
        # was only reading more than the fixture had copied.
        inputs=sorted(set(TITLEBAR_INPUT.findall((source/TITLEBAR).read_text())))
        self.assertIn('tauri.conf.json',inputs,'the real check still reads the Tauri config')
        self.assertIn('frontend/src/local/ConnectedApp.tsx',inputs)
        for name in [TITLEBAR,*inputs]:
            dest=self.root/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source/name,dest)
        service,task=self.create(test_file=TITLEBAR,profile='node-test')
        attempt=service.verify_current(task['id'],**self.ctx)
        self.assertEqual(set(attempt['check_inputs']),set(inputs),attempt.get('check_input_blocker'))
        self.assertEqual(attempt['files'],[TITLEBAR])
        job=service.launch(task['id'],attempt_number=attempt['number'],**self.ctx)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=service.poll(task['id'],job_id=job,**self.ctx)
            if result['state']!='running': break
            time.sleep(.02)
        self.assertEqual(result['state'],'verified',result)
        self.assertEqual(result['test']['exit_status'],0,result)
        self.assertTrue(service._current(task,result))
        with (self.root/'frontend/src/local/ConnectedApp.tsx').open('a') as stream: stream.write('\n// changed\n')
        self.assertFalse(service._current(task,result),'a frozen input that changes expires the attempt')
    def test_collector_and_missing_dependency_blocker(self):
        (self.root/'lib.mjs').write_text('export const value = 1;')
        (self.root/'test_local.mjs').write_text("import './lib.mjs';")
        task={'root':str(self.root),'root_identity':project_access.root_identity(self.root),'check_profile':'node-test'}
        self.assertEqual(set(project_access.collect(task,'test_local.mjs')),{'test_local.mjs','lib.mjs'})
        for source in ["import './missing.mjs';","import 'unknown-package';",'import(name);','new URL(name, import.meta.url)']:
            (self.root/'test_local.mjs').write_text(source)
            with self.subTest(source=source),self.assertRaises(Rejected): project_access.collect(task,'test_local.mjs')
        (self.root/'test_local.mjs').write_text("import './missing.mjs';")
        service,record=self.create(test_file='test_local.mjs',profile='node-test')
        attempt=service.verify_current(record['id'],**self.ctx)
        with self.assertRaisesRegex(Rejected,'missing'): service.launch(record['id'],attempt_number=attempt['number'],**self.ctx)
    def test_python_package_inputs(self):
        (self.root/'pkg').mkdir(); (self.root/'pkg/__init__.py').write_text(''); (self.root/'pkg/value.py').write_text('value = 1\n')
        (self.root/'test_sample.py').write_text('from pkg.value import value\nimport unittest\n')
        service,task=self.create()
        self.assertEqual(set(project_access.collect(task,'test_sample.py')),{'test_sample.py','pkg/value.py','pkg/__init__.py'})

class ProjectGrantTests(unittest.TestCase):
    provider='codex'
    setUp=ConversationFixture.setUp
    close_host=ConversationFixture.close_host
    def grant(self):
        return self.host.dispatch('coding_permission_grant',{**self.scope,'files':['test_sample.py'],'testFile':'test_sample.py','projectAccess':True})
    def test_explicit_grant_and_revoke_existing_file(self):
        self.assertTrue(self.host.dispatch('coding_permission',self.scope)['project_access_available'])
        self.assertTrue(self.grant()['project_access'])
        task=self.host.dispatch('task_create',{**self.scope,'text':'Check source access','files':['test_sample.py'],'testFile':'test_sample.py','projectAccess':True})
        raw=self.host.coding.read(task['id'],dict(thread_id='c',project_id='p',actor='local-user'))
        raw['origin']='conversation'; self.host.coding.save(raw)
        self.host.coding.require_grant_for(raw,'test_sample.py')
        self.host.dispatch('coding_permission_revoke',self.scope)
        with self.assertRaisesRegex(Rejected,'revoked'): self.host.coding.require_grant_for(raw,'test_sample.py')
        with self.assertRaises(Rejected): self.host.coding.require_grant(raw)
        with self.assertRaisesRegex(Rejected,'revoked'):
            self.host.coding.collaboration.open_packet(raw,{},'review','review source','',[],token='test',depth=1,queued=None,child_collab={})
    def test_helper_keeps_narrow_scope_but_loses_access_when_owner_grant_is_restricted(self):
        self.grant()
        common={**self.scope,'text':'Check source access','files':['test_sample.py'],'testFile':'test_sample.py'}
        owner=self.host.dispatch('task_create',{**common,'projectAccess':True})
        owner['origin']='conversation'; self.host.coding.save(owner)
        child=self.host.dispatch('task_create',common)
        child.update(origin='collaboration',collaboration={'root_task_id':owner['id']})
        self.host.coding.save(child)
        self.assertFalse(child.get('project_access'))
        self.host.coding.require_grant_for(child,'test_sample.py')
        self.host.dispatch('coding_permission_grant',{**self.scope,'files':['test_sample.py'],'testFile':'test_sample.py'})
        with self.assertRaisesRegex(Rejected,'restricted'): self.host.coding.require_grant_for(child,'test_sample.py')
        with self.assertRaisesRegex(Rejected,'restricted'): self.host.coding.require_grant(child)

    def test_legacy_grant_is_not_promoted(self):
        args={**self.scope,'files':['test_sample.py'],'testFile':'test_sample.py'}
        self.assertFalse(self.host.dispatch('coding_permission_grant',args)['project_access'])
        for extra in [{'projectAccess':'yes'},{'projectAccess':True,'folders':['src']},{'projectAccess':True,'files':['greeting.py','test_sample.py']}]:
            with self.subTest(extra=extra),self.assertRaises(Rejected): self.host.dispatch('coding_permission_grant',{**args,**extra})
        self.assertFalse(self.host.dispatch('coding_permission',self.scope)['project_access'])
    def test_host_store_containment(self):
        (self.base/'test_sample.py').write_text('import unittest\n')
        self.host.store.add_project('outer',self.base,'p','synthetic-model'); self.host.store.add_thread('outer','outer','outer','private','local-user')
        with self.assertRaises(Rejected): self.host.dispatch('coding_permission_grant',dict(projectId='outer',channelId='outer',files=['test_sample.py'],testFile='test_sample.py',projectAccess=True))
