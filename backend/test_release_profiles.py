"""Release gates use generated bundle configuration, never environment or user settings."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import release_profiles as profiles
import account_config
from test_supabase_client import PROJECT
from release_preview import running_release


class ReleaseProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_beta_is_default_and_cannot_enable_openrouter_through_environment(self):
        with patch.dict(os.environ, {'DERVO_OPENROUTER_ENABLED':'1', 'DERVO_RELEASE_PROFILE':'personal-experimental'}):
            self.assertFalse(profiles.read(self.root)['openRouter'])
            profiles.stage(self.root, 'private-beta')
            with self.assertRaises(ValueError): profiles.require_provider('openrouter', self.root)
            for provider in ('claude', 'codex'): profiles.require_provider(provider, self.root)

    def test_personal_profile_is_explicit_and_does_not_change_beta_configuration(self):
        personal = profiles.stage(self.root, 'personal-experimental')
        profiles.require_provider('openrouter', self.root)
        self.assertEqual(personal['label'], 'Personal / Experimental')
        profiles.stage(self.root, 'private-beta')
        with self.assertRaises(ValueError): profiles.require_provider('openrouter', self.root)

    def test_modified_or_unknown_profile_is_rejected(self):
        record = profiles.stage(self.root, 'private-beta')
        for change in [{'openRouter':True}, {'profile':'unknown'}, {'privateKey':'not-a-real-key'}]:
            (self.root/'runtime'/profiles.MARKER).write_text(json.dumps({**record, **change}))
            with self.assertRaises(ValueError): profiles.read(self.root)

    def test_packaging_requires_the_exact_requested_profile(self):
        with self.assertRaises(ValueError): profiles.verify_bundle(self.root, 'private-beta')
        profiles.stage(self.root, 'personal-experimental')
        with self.assertRaises(ValueError): profiles.verify_bundle(self.root, 'private-beta')
        account_config.stage(self.root, PROJECT)
        profiles.verify_bundle(self.root, 'personal-experimental')

    def test_packaging_rejects_missing_and_linked_manifests(self):
        runtime = self.root/'runtime'
        runtime.mkdir()
        marker = runtime/profiles.MARKER
        marker.symlink_to(self.root/'missing.json')
        with self.assertRaises(ValueError): profiles.verify_bundle(self.root, 'private-beta')
        with self.assertRaises(ValueError): profiles.read(self.root)
        marker.unlink(); runtime.rmdir()
        other = self.root/'other'; other.mkdir()
        runtime.symlink_to(other, target_is_directory=True)
        (other/profiles.MARKER).write_text(json.dumps(profiles.manifest()))
        with self.assertRaises(ValueError): profiles.verify_bundle(self.root, 'private-beta')

    def test_running_diagnostics_include_the_bundled_profile(self):
        profiles.stage(self.root, 'private-beta')
        (self.root/'runtime/dervo-preview.json').write_text(json.dumps({'releaseId':'fixture-1'}))
        self.assertEqual(running_release(self.root), {'id':'fixture-1','profile':'private-beta','label':'Private beta'})

    def test_packaging_rejects_workspace_files_and_keys_without_printing_secret(self):
        profiles.stage(self.root, 'private-beta')
        account_config.stage(self.root, PROJECT)
        file = self.root/'runtime'/'auth.json'
        file.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Local account'): profiles.verify_bundle(self.root, 'private-beta')
        file.unlink()
        secret = 'sk-or-' + 'v1-' + 'x' * 40
        file = self.root/'runtime'/'accidental.txt'
        file.write_text(secret)
        with self.assertRaises(ValueError) as caught: profiles.verify_bundle(self.root, 'private-beta')
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn('accidental.txt', str(caught.exception))


    def test_runtime_exclusion_keeps_source_tests_and_production_module(self):
        backend = self.root/'backend'; backend.mkdir()
        source = self.root/'source'; source.mkdir()
        (source/'test_probe.py').write_text('private development fixture')
        (backend/'test_probe.py').write_text((source/'test_probe.py').read_text())
        (backend/'runner.py').write_text('production module')
        removed = profiles.exclude_test_modules(self.root)
        self.assertEqual(removed, ['backend/test_probe.py'])
        self.assertTrue((source/'test_probe.py').exists())
        self.assertEqual((backend/'runner.py').read_text(), 'production module')

    def test_bundle_verifier_rejects_tests_if_exclusion_was_missed(self):
        profiles.stage(self.root, 'private-beta')
        account_config.stage(self.root, PROJECT)
        (self.root/'backend').mkdir()
        (self.root/'backend/test_probe.py').write_text('private development fixture')
        with self.assertRaisesRegex(ValueError, 'Development test module'):
            profiles.verify_bundle(self.root, 'private-beta')
        profiles.exclude_test_modules(self.root)
        profiles.verify_bundle(self.root, 'private-beta')

    def test_runtime_exclusion_refuses_links_before_deleting_anything(self):
        backend = self.root/'backend'; backend.mkdir()
        (backend/'test_first.py').write_text('keep')
        (backend/'test_link.py').symlink_to(backend/'test_first.py')
        with self.assertRaises(ValueError): profiles.exclude_test_modules(self.root)
        self.assertTrue((backend/'test_first.py').exists())


if __name__ == '__main__': unittest.main()
