"""Profile and config checks for the project trust Codex writes into its own home (0.154.0, and 0.156.1 unchanged). Temp
folders only."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from codex_policy import POLICY, check_profile, verify_config
from dervo_connections import Rejected

# Byte for byte what a full-access run left in the founder's profile on 23 Sep (task fb5aa438), 60 bytes.
TRUST = '[projects."/Users/bryce/dervo/app"]\ntrust_level = "trusted"\n'
TRUST_TABLE = {'projects': {'/Users/bryce/dervo/app': {'trust_level': 'trusted'}}}
REFUSED = {
    'another top-level key': 'model = "gpt-5.5"\n' + TRUST,
    'a hook': TRUST + '[hooks]\nstop = ["/bin/sh", "-c", "true"]\n',
    'a model provider': TRUST + '[model_providers.planted]\nname = "planted"\nbase_url = "http://127.0.0.1:9/v1"\n',
    'a notify command': 'notify = ["/bin/sh", "-c", "true"]\n' + TRUST,
    'a relative project key': '[projects."dervo/app"]\ntrust_level = "trusted"\n',
    'an extra key in a project table': TRUST + 'sandbox_mode = "danger-full-access"\n',
    'another trust value': '[projects."/Users/bryce/dervo/app"]\ntrust_level = "always"\n',
    'a project without trust': '[projects."/Users/bryce/dervo/app"]\n',
    'projects as a string': 'projects = "/Users/bryce/dervo/app"\n',
    'projects as an array of tables': '[[projects]]\ntrust_level = "trusted"\n',
    'a file that is not TOML': '[projects."/Users/bryce/dervo/app"\ntrust_level = trusted\n',
    'an empty file': '',
    'a file over 64 KB': ('# padding\n' * 7000) + TRUST,
}


class ProfileTests(unittest.TestCase):
    def home(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='dervo-codex-trust-')))
        return home

    def test_the_trust_file_codex_writes_passes(self):
        home = self.home()
        (home / 'config.toml').write_text(TRUST)
        self.assertEqual((home / 'config.toml').stat().st_size, 60)
        check_profile(home)
        (home / 'config.toml').write_text(TRUST + '\n[projects."/Users/bryce/other"]\ntrust_level = "untrusted"\n')
        check_profile(home)

    def test_anything_more_than_trust_is_still_refused(self):
        for name, text in REFUSED.items():
            with self.subTest(name):
                home = self.home()
                (home / 'config.toml').write_text(text)
                with self.assertRaisesRegex(Rejected, 'needs review: config.toml'):
                    check_profile(home)

    @unittest.skipUnless(os.name == 'nt', 'Windows has no O_NOFOLLOW, so it reads the file another way')
    def test_a_windows_trust_file_passes_and_anything_more_is_refused(self):
        # Every Codex run on Windows stopped here with AttributeError before 2 Oct. Codex writes Windows keys there.
        home = self.home()
        (home / 'config.toml').write_text("[projects.'C:\\Users\\tester\\project']\ntrust_level = \"trusted\"\n")
        check_profile(home)
        (home / 'config.toml').write_text("model = \"gpt-5.5\"\n[projects.'C:\\Users\\tester\\project']\ntrust_level = \"trusted\"\n")
        with self.assertRaisesRegex(Rejected, 'needs review: config.toml'):
            check_profile(home)

    def test_a_linked_or_misplaced_trust_file_is_refused(self):
        home = self.home()
        elsewhere = home / 'elsewhere.toml'
        elsewhere.write_text(TRUST)
        os.symlink(elsewhere, home / 'config.toml')
        with self.assertRaisesRegex(Rejected, 'needs review: config.toml'):
            check_profile(home)
        home = self.home()
        (home / 'config.toml').mkdir()
        with self.assertRaisesRegex(Rejected, 'needs review: config.toml'):
            check_profile(home)
        home = self.home()
        (home / '.codex').mkdir()
        (home / '.codex' / 'config.toml').write_text(TRUST)
        with self.assertRaisesRegex(Rejected, r'needs review: \.codex/config\.toml'):
            check_profile(home)

    def test_trust_doesnt_excuse_hooks_plugins_or_skills(self):
        for relative in ('hooks.json', 'plugins', '.agents'):
            with self.subTest(relative):
                home = self.home()
                (home / 'config.toml').write_text(TRUST)
                (home / relative).write_text('{}')
                with self.assertRaisesRegex(Rejected, 'needs review: ' + relative):
                    check_profile(home)
        home = self.home()
        (home / 'config.toml').write_text(TRUST)
        (home / 'skills' / 'mine').mkdir(parents=True)
        with self.assertRaisesRegex(Rejected, 'skills need review'):
            check_profile(home)


def config_read(argv, *layers):
    """What Codex's config/read (0.154.0 and 0.156.1) reports for a launch with ``argv`` and no config of its own: the -c flags, read as
    TOML the way Codex reads them, as the session layer and as the effective values, then ``layers``."""
    import tomllib
    flags = {}
    for flag, value in zip(argv, argv[1:]):
        if flag == '-c':
            flags.update(tomllib.loads(value))
    return {'config': deepcopy(flags), 'layers': [{'name': {'type': 'sessionFlags'}, 'config': flags}, *layers]}


def user(config):
    return {'name': {'type': 'user', 'file': '/private/tmp/profile/config.toml', 'profile': None}, 'config': config}


def project(config, disabled=None):
    layer = {'name': {'type': 'project', 'dotCodexFolder': '/Users/bryce/dervo/app/.codex'}, 'config': config}
    if disabled is not None:
        layer['disabledReason'] = disabled
    return layer


def read(*layers, policy=POLICY, effective=None):
    """A config/read result: Dervo's own flags first, the other layers after, as Codex 0.154.0 and 0.156.1 order them."""
    config = {**deepcopy(policy), **(effective or {})}
    return {'config': config, 'layers': [{'name': {'type': 'sessionFlags'}, 'config': deepcopy(policy)}, *layers]}


class ConfigTests(unittest.TestCase):
    def verifiers(self):
        from codex_coding_policy import policy_for, verify_config as verify_coding
        return (('chat', POLICY, verify_config),
                ('coding', policy_for(False), verify_coding),
                ('full access', policy_for(True), lambda result: verify_coding(result, full_access=True)))

    def test_a_trust_only_user_layer_is_accepted(self):
        for name, policy, verify in self.verifiers():
            with self.subTest(name):
                verify(read(user(deepcopy(TRUST_TABLE)), policy=policy, effective=deepcopy(TRUST_TABLE)))
                verify(read(user({'projects': {'/a': {'trust_level': 'untrusted'}, '/b': {'trust_level': 'trusted'}}}), policy=policy))

    def test_a_user_layer_with_anything_else_is_refused(self):
        changes = [{**TRUST_TABLE, 'model': 'gpt-5.5'}, {**TRUST_TABLE, 'notify': ['/bin/sh']},
                   {**TRUST_TABLE, 'model_providers': {'planted': {'base_url': 'http://127.0.0.1:9/v1'}}},
                   {'projects': {'/Users/bryce/dervo/app': {'trust_level': 'trusted', 'sandbox_mode': 'danger-full-access'}}},
                   {'projects': {'dervo/app': {'trust_level': 'trusted'}}},
                   {'projects': {'/Users/bryce/dervo/app': {'trust_level': 'always'}}},
                   {'projects': {'/Users/bryce/dervo/app': {}}}, {'projects': []}, {'model': 'gpt-5.5'}]
        for name, policy, verify in self.verifiers():
            for config in changes:
                with self.subTest(name, config=config), self.assertRaisesRegex(Rejected, 'Custom or managed'):
                    verify(read(user(config), policy=policy))

    def test_trust_is_accepted_only_from_the_user_layer(self):
        for kind in ('system', 'legacyManagedConfigTomlFromFile', 'mdm'):
            with self.subTest(kind), self.assertRaisesRegex(Rejected, 'Custom or managed'):
                verify_config(read({'name': {'type': kind, 'file': '/etc/codex/config.toml'}, 'config': deepcopy(TRUST_TABLE)}))

    def test_a_repo_config_that_trust_switched_on_is_refused(self):
        # Trusted, Codex loads the repo's .codex/config.toml as a live project layer. That never runs.
        planted = {'model_reasoning_effort': 'minimal'}
        for name, policy, verify in self.verifiers():
            with self.subTest(name):
                with self.assertRaisesRegex(Rejected, 'Custom or managed'):
                    verify(read(project(planted), user(deepcopy(TRUST_TABLE)), policy=policy))
                with self.assertRaisesRegex(Rejected, 'Custom or managed'):
                    verify(read(project(planted, disabled='  '), user(deepcopy(TRUST_TABLE)), policy=policy))
                # Untrusted, Codex reports the same file as not loaded, which is only where it came from.
                verify(read(project(planted, disabled='To load project-local config, add it as a trusted project.'),
                            user({'projects': {'/Users/bryce/dervo/app': {'trust_level': 'untrusted'}}}), policy=policy))

    def test_trust_doesnt_change_the_effective_checks(self):
        for name, policy, verify in self.verifiers():
            for effective in ({'sandbox_mode': 'danger-full-access' if name != 'full access' else 'read-only'},
                              {'notify': ['/bin/sh']}, {'model_providers': {'planted': {}}}):
                with self.subTest(name, effective=effective), self.assertRaises(Rejected):
                    verify(read(user(deepcopy(TRUST_TABLE)), policy=policy, effective=effective))


class FolderTests(unittest.TestCase):
    """Every launch marks its folder untrusted. Codex 0.154.0 then writes no trust and loads no repo .codex settings,
    measured on the bundled binary on 23 Sep for a repo root, a subfolder and a git worktree. 0.156.1 did the same for a
    repo root on 25 Sep (evidence/codex-local-upgrade/probe-0156.json)."""
    CWD = '/Users/bryce/dervo/app'
    PLANTED = {'model_reasoning_effort': 'minimal'}

    def launches(self):
        import codex_coding_policy
        from codex_policy import argv
        cwd = self.CWD
        return (('chat', lambda **kw: argv('/fixture/codex', **kw), lambda r: verify_config(r, cwd=cwd)),
                ('coding', lambda **kw: codex_coding_policy.argv(**kw),
                 lambda r: codex_coding_policy.verify_config(r, cwd=cwd)),
                ('full access', lambda **kw: codex_coding_policy.argv(full_access=True, **kw),
                 lambda r: codex_coding_policy.verify_config(r, full_access=True, cwd=cwd)))

    def test_the_folder_and_every_folder_above_it_are_untrusted(self):
        from codex_policy import untrusted_projects
        self.assertEqual(untrusted_projects(self.CWD), {path: {'trust_level': 'untrusted'} for path in
                         ('/Users/bryce/dervo/app', '/Users/bryce/dervo', '/Users/bryce', '/Users', '/')})
        with tempfile.TemporaryDirectory() as root:
            real = Path(root).resolve() / 'repo'
            real.mkdir()
            os.symlink(real, Path(root) / 'link')
            folders = untrusted_projects(str(Path(root) / 'link'))
            self.assertIn(str(Path(root) / 'link'), folders)
            self.assertIn(str(real), folders)
            self.assertIn(str(real.parent), folders)
        for cwd in ('dervo/app', '', None, '/Users/bryce/\0app'):
            with self.subTest(cwd=cwd), self.assertRaises(Rejected):
                untrusted_projects(cwd)

    def test_each_launch_passes_the_table_and_its_check_accepts_it(self):
        import tomllib
        from codex_policy import untrusted_projects
        for name, build, verify in self.launches():
            with self.subTest(name):
                args = build(cwd=self.CWD)
                table = [tomllib.loads(v) for f, v in zip(args, args[1:]) if f == '-c' and v.startswith('projects=')]
                self.assertEqual(table, [{'projects': untrusted_projects(self.CWD)}])
                verify(config_read(args))
                # A launch without the table (every build before this one) doesn't pass the check for a folder.
                with self.assertRaisesRegex(Rejected, 'not applied exactly'):
                    verify(config_read(build()))

    def test_trust_left_for_other_folders_is_fine_but_never_for_this_one(self):
        for name, build, verify in self.launches():
            with self.subTest(name):
                result = config_read(build(cwd=self.CWD), user({'projects': {'/somewhere/else': {'trust_level': 'trusted'}}}))
                result['config']['projects']['/somewhere/else'] = {'trust_level': 'trusted'}
                verify(result)
                for projects in ({**result['config']['projects'], self.CWD: {'trust_level': 'trusted'}},
                                 {'/somewhere/else': {'trust_level': 'trusted'}}, None,
                                 {**result['config']['projects'], '/somewhere/else': {'trust_level': 'trusted', 'x': 1}}):
                    changed = deepcopy(result)
                    changed['config']['projects'] = projects
                    with self.assertRaisesRegex(Rejected, 'would trust this project folder'):
                        verify(changed)

    def test_a_trusted_repo_with_its_own_settings_runs_with_them_unloaded(self):
        # What Codex reported for a profile holding the founder's trust entry and a repo with .codex/config.toml, once
        # the launch marked the folder untrusted: the project layer disabled and the user layer unchanged.
        for name, build, verify in self.launches():
            with self.subTest(name):
                disabled = project(self.PLANTED, disabled=self.CWD + ' is marked as untrusted in the effective configuration.')
                result = config_read(build(cwd=self.CWD), disabled, user(deepcopy(TRUST_TABLE)))
                verify(result)
                # The same folder without the launch's table: Codex loads the repo settings, and that's refused.
                trusted = config_read(build(), project(self.PLANTED), user(deepcopy(TRUST_TABLE)))
                with self.assertRaisesRegex(Rejected, 'Custom or managed'):
                    import codex_coding_policy
                    (verify_config(trusted) if name == 'chat' else
                     codex_coding_policy.verify_config(trusted, full_access=name == 'full access'))


if __name__ == '__main__':
    unittest.main()
