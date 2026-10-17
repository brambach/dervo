"""Account configuration is required in release artifacts, never a private credential."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import account_config as config
import release_profiles
from dervo_connections import Rejected
from test_supabase_client import PROJECT
import test_release_storage as storage_tests


class AccountPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def test_source_without_config_is_local_but_packaged_missing_config_denies(self):
        self.assertIsNone(config.create(None,self.root/'data',self.root))
        release_profiles.stage(self.root,'private-beta')
        runtime=config.create(None,self.root/'data',self.root)
        self.assertIsInstance(runtime,config.UnavailableAccount)
        with self.assertRaises(Rejected):runtime.require_access()
        with self.assertRaises(Rejected):release_profiles.verify_bundle(self.root,'private-beta')
        packaged=self.root/'Candidate.app/Contents/Resources'
        packaged.mkdir(parents=True)
        self.assertIsInstance(config.create(None,self.root/'data',packaged),config.UnavailableAccount)

    def test_public_input_is_validated_and_staged_without_extra_fields(self):
        path=self.root/'public.json'
        public=dict(project_ref=PROJECT.project_ref,url=PROJECT.url,publishable_key=PROJECT.publishable_key)
        path.write_text(json.dumps(public))
        project=config.for_build(self.root,{'accountPublicConfig':str(path)})
        config.stage(self.root,project)
        self.assertEqual(config.require(self.root),PROJECT)
        release_profiles.stage(self.root,'private-beta')
        self.assertEqual(release_profiles.verify_bundle(self.root,'private-beta')['publicAccountConfiguration'],'validated')
        self.assertEqual(config.for_build(self.root,{}),PROJECT)
        for document in [dict(public,service_role='private-value'),dict(public,publishable_key='sb_secret_'+'x'*40),{'access_token':'private-value'}]:
            path.write_text(json.dumps(document))
            with self.assertRaises(Rejected) as caught:config.for_build(self.root,{'accountPublicConfig':str(path)})
            self.assertNotIn('private-value',str(caught.exception))
            self.assertNotIn('sb_secret_',str(caught.exception))

    def test_linked_public_input_and_missing_config_are_refused(self):
        with self.assertRaises(Rejected):config.for_build(self.root,{})
        target=self.root/'real.json';target.write_text('{}')
        linked=self.root/'linked.json';linked.symlink_to(target)
        with self.assertRaises(Rejected):config.for_build(self.root,{'accountPublicConfig':str(linked)})

    def test_secret_patterns_are_rejected_without_values(self):
        release_profiles.stage(self.root,'private-beta');config.stage(self.root,PROJECT)
        path=self.root/'accidental.txt'
        for prefix in ('sb_secret_','sk-ant-api03-','sk-or-v1-'):
            value=prefix+'x'*40;path.write_text(value)
            with self.assertRaises(ValueError) as caught:release_profiles.verify_bundle(self.root,'private-beta')
            self.assertNotIn(value,str(caught.exception))
        import base64
        encoded=lambda value:base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
        token=encoded({'alg':'HS256'})+'.'+encoded({'role':'service_role'})+'.'+'x'*32
        path.write_bytes(b' '*(1024*1024-8)+token.encode())
        with self.assertRaises(ValueError) as caught:release_profiles.verify_bundle(self.root,'private-beta')
        self.assertNotIn(token,str(caught.exception))


class BuilderAccountTests(unittest.TestCase):
    setUp=storage_tests.StorageTests.setUp
    fake_command=storage_tests.StorageTests.fake_command

    def test_missing_bundled_account_is_refused_before_signing(self):
        def omit(command,**kwargs):
            result=self.fake_command(command,**kwargs)
            if 'build' in command and '--bundles' in command:
                (Path(kwargs['env']['CARGO_TARGET_DIR'])/'release/bundle/macos/Dervo Local.app/Contents/Resources/runtime'/config.NAME).unlink()
            return result
        import sys
        from release_builder import build
        with patch('release_builder.BuildStorage',return_value=self.storage),patch('release_builder.shutil.which',return_value=sys.executable),patch('release_builder.subprocess.run',side_effect=omit) as commands:
            with self.assertRaises(Rejected):build(self.source,self.root/'missing-account',{})
        self.assertFalse(any(c.args[0][0]=='/usr/bin/codesign' for c in commands.call_args_list))

    def test_wrong_bundled_project_is_refused_before_signing(self):
        from supabase_client import PublicProject
        def swap(command,**kwargs):
            result=self.fake_command(command,**kwargs)
            if 'build' in command and '--bundles' in command:
                config.stage(Path(kwargs['env']['CARGO_TARGET_DIR'])/'release/bundle/macos/Dervo Local.app/Contents/Resources',PublicProject('b'*20,'https://'+'b'*20+'.supabase.co',PROJECT.publishable_key))
            return result
        import sys
        from release_builder import build
        with patch('release_builder.BuildStorage',return_value=self.storage),patch('release_builder.shutil.which',return_value=sys.executable),patch('release_builder.subprocess.run',side_effect=swap) as commands:
            with self.assertRaisesRegex(RuntimeError,'differs from the requested public'):build(self.source,self.root/'wrong-account',{})
        self.assertFalse(any(c.args[0][0]=='/usr/bin/codesign' for c in commands.call_args_list))
