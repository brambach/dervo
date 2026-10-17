"""Finite cached declaration/config inputs. No installs or provider calls."""
import base64
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import check_profiles as profiles
from dervo_connections import Rejected
import task_evidence
import typescript_inputs
from test_check_profiles import install_compiler, cached_compiler, node_available

class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = {'compilerOptions': {'strict': True, 'noEmit': True}, 'files': ['src/a.ts'],
                       'dervoInputs': ['tsconfig.base.json', 'node_modules/example/package.json',
                                       'node_modules/example/index.d.ts', 'node_modules/example/LICENSE'],
                       'extends': './tsconfig.base.json'}
        self.put('tsconfig.base.json', '{"compilerOptions":{"target":"ES2020"}}')
        self.put('node_modules/example/package.json', '{"name":"example","types":"index.d.ts"}')
        self.put('node_modules/example/index.d.ts', 'export const value: string;')
        self.put('node_modules/example/LICENSE', 'Original fixture licence')
        self.put('src/a.ts', 'export const a = 1;')
        self.save()
    def put(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
    def save(self):
        self.put('tsconfig.json', json.dumps(self.config))
    def test_exact_hashes_and_config_closure(self):
        record = typescript_inputs.collect(self.root)
        self.assertEqual(set(record), set(self.config['dervoInputs']) | {'tsconfig.json'})
        self.put('node_modules/example/index.d.ts', 'export const value: number;')
        self.assertNotEqual(record, typescript_inputs.collect(self.root))
        self.config['dervoInputs'].remove('tsconfig.base.json')
        self.save()
        with self.assertRaisesRegex(Rejected, 'explicit'):
            typescript_inputs.collect(self.root)
    def test_scope_symlinks_executables_sensitive_files_and_caps(self):
        for path in ('../outside.d.ts', '/tmp/a.d.ts', 'node_modules/example/build.js',
                     'node_modules/example/.env', 'credentials/key.d.ts', 'node_modules/example/auth-token.d.ts'):
            with self.subTest(path=path):
                self.config['dervoInputs'].append(path)
                self.save()
                with self.assertRaises(Rejected):
                    typescript_inputs.collect(self.root)
                self.config['dervoInputs'].pop()
        self.save()
        target = self.root / 'node_modules/example/index.d.ts'
        target.unlink()
        target.symlink_to(self.root / 'src/a.ts')
        with self.assertRaises(Rejected):
            typescript_inputs.collect(self.root)
        target.unlink()
        target.write_text('export {}')
        for cap, value in (('BYTE_CAP', 10), ('FILE_CAP', 2)):
            with patch.object(typescript_inputs, cap, value):
                with self.assertRaisesRegex(Rejected, 'cap'):
                    typescript_inputs.collect(self.root)
    def test_configs_cannot_skip_check_or_load_plugins_references_or_unlisted_configs(self):
        for extra in ({'references': [{'path': './other.json'}]}, {'files': []},
                      {'compilerOptions': {'noCheck': True}}, {'compilerOptions': {'plugins': [{'name': 'evil'}]}},
                      {'extends': 'external-config'}, {'extends': './missing.json'}):
            original = dict(self.config)
            self.config.update(extra)
            self.save()
            with self.assertRaises(Rejected):
                typescript_inputs.collect(self.root)
            self.config = original
        self.save()
        self.put('tsconfig.base.json', '{"extends":"./tsconfig.json"}')
        with self.assertRaisesRegex(Rejected, 'cycle'):
            typescript_inputs.collect(self.root)
    def test_missing_licence_and_escaping_package_metadata_refused(self):
        self.config['dervoInputs'].remove('node_modules/example/LICENSE')
        self.save()
        with self.assertRaisesRegex(Rejected, 'licen'):
            typescript_inputs.collect(self.root)
        self.config['dervoInputs'].append('node_modules/example/LICENSE')
        self.save()
        self.put('node_modules/example/package.json', '{"name":"example","types":"../../secret.d.ts"}')
        with self.assertRaises(Rejected):
            typescript_inputs.collect(self.root)

    def test_original_tauri_licence_filenames_are_data_inputs(self):
        for name in ('LICENSE_APACHE-2.0', 'LICENSE_MIT'):
            self.assertEqual(typescript_inputs._kind('node_modules/@tauri-apps/api/' + name),
                             ('licence', 'node_modules/@tauri-apps/api'))
        for name in ('LICENSE_MIT.js', 'LICENSE_APACHE-2.0.cjs'):
            with self.assertRaises(Rejected):
                typescript_inputs._kind('node_modules/@tauri-apps/api/' + name)
    def test_read_race_and_directory_symlinks_are_refused(self):
        original_read = os.read
        changed = False
        target = self.root / 'node_modules/example/index.d.ts'
        def mutate(fd, size):
            nonlocal changed
            data = original_read(fd, size)
            if data == b'export const value: string;' and not changed:
                changed = True
                target.write_text('export const value: number;')
            return data
        with patch.object(typescript_inputs.os, 'read', mutate):
            with self.assertRaisesRegex(Rejected, 'changed while'):
                typescript_inputs.collect(self.root)
        package = self.root / 'node_modules/example'
        renamed = self.root / 'cached-example'
        package.rename(renamed)
        package.symlink_to(renamed, target_is_directory=True)
        with self.assertRaisesRegex(Rejected, 'symlink'):
            typescript_inputs.collect(self.root)
        package.unlink()
        package.symlink_to(self.root.parent / 'outside-package', target_is_directory=True)
        with self.assertRaisesRegex(Rejected, 'symlink'):
            typescript_inputs.collect(self.root)

    def test_build_scripts_are_inert_metadata_and_cannot_be_inputs(self):
        self.put('node_modules/example/package.json', '{"name":"example","types":"index.d.ts","scripts":{"preinstall":"touch stolen","build":"curl external"}}')
        with patch.object(task_evidence.subprocess, 'Popen', side_effect=AssertionError('metadata must not execute')):
            record = typescript_inputs.collect(self.root)
        self.assertIn(b'preinstall', record['node_modules/example/package.json'])
        self.assertFalse((self.root / 'stolen').exists())
        self.config['dervoInputs'].append('node_modules/example/build.js')
        self.save()
        with self.assertRaisesRegex(Rejected, 'build-script'):
            typescript_inputs.collect(self.root)

    def test_empty_snapshot_is_not_a_project_check(self):
        content = typescript_inputs.collect(self.root)
        with self.assertRaisesRegex(Rejected, 'source'):
            typescript_inputs.validate_snapshot(content)
        content['src/a.ts'] = b'export const a = 1;'
        typescript_inputs.validate_snapshot(content)
        for config in ({'files': []}, {'files': ['only.d.ts']}, {'compilerOptions': {'noCheck': True}}):
            with self.assertRaises(Rejected):
                typescript_inputs.validate_snapshot({'tsconfig.json': json.dumps(config).encode(), 'src/a.ts': b'export {}', 'only.d.ts': b'export {}'})


class PreviewJsonTests(InputTests):
    def setUp(self):
        super().setUp()
        self.config['dervoInputs'].append('preview-features.json')
        self.manifest = {'version': 1, 'features': [{'id': 'search', 'name': 'Search',
            'description': 'Search saved records', 'platforms': ['desktop']}]}
        self.put('preview-features.json', json.dumps(self.manifest))
        self.save()

    def test_known_json_is_copied_and_changes_its_hash(self):
        before = typescript_inputs.collect(self.root)
        self.assertEqual(json.loads(before['preview-features.json']), self.manifest)
        self.manifest['features'][0]['name'] = 'Saved search'
        self.put('preview-features.json', json.dumps(self.manifest))
        after = typescript_inputs.collect(self.root)
        self.assertNotEqual(hashlib.sha256(before['preview-features.json']).hexdigest(),
                            hashlib.sha256(after['preview-features.json']).hexdigest())

    def test_private_json_paths_refused_before_any_input_read(self):
        for path in ('private.json', 'scripts/model-capabilities.json', 'src/preview-features.json',
                     'credentials/preview-features.json', '../preview-features.json'):
            self.config['dervoInputs'].append(path)
            self.save()
            read = typescript_inputs._read
            seen = []
            def tracked(root, name, remaining):
                seen.append(name)
                return read(root, name, remaining)
            with patch.object(typescript_inputs, '_read', tracked):
                with self.assertRaises(Rejected):
                    typescript_inputs.collect(self.root)
            self.assertEqual(seen, ['tsconfig.json'])
            self.config['dervoInputs'].pop()

    def test_manifest_schema_and_bounds_refuse_private_or_wrong_data(self):
        for value in ([], {'version': True, 'features': []}, {'version': 1, 'features': [], 'token': 'secret'},
                      {'version': 1, 'features': [{'id': 'a', 'name': 'A', 'description': 'x', 'token': 'secret'}]},
                      {'version': 1, 'features': [{'id': 'a', 'name': 'A', 'description': 'x', 'platforms': ['private']}]},
                      {'version': 1, 'features': [self.manifest['features'][0]] * 129}):
            with self.subTest(value=str(value)[:100]):
                self.put('preview-features.json', json.dumps(value))
                with self.assertRaisesRegex(Rejected, 'manifest|JSON|must be an object'):
                    typescript_inputs.collect(self.root)
        self.put('preview-features.json', ' ' * 65537)
        with self.assertRaisesRegex(Rejected, 'cap'):
            typescript_inputs.collect(self.root)

    def test_json_symlink_hardlink_fifo_and_read_race_refused(self):
        target = self.root / 'preview-features.json'
        data = target.read_bytes()
        target.unlink()
        target.symlink_to(self.root / 'src/a.ts')
        with self.assertRaises(Rejected):
            typescript_inputs.collect(self.root)
        target.unlink()
        os.link(self.root / 'src/a.ts', target)
        with self.assertRaises(Rejected):
            typescript_inputs.collect(self.root)
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(Rejected):
            typescript_inputs.collect(self.root)
        target.unlink()
        target.write_bytes(data)
        read = os.read
        def mutate(fd, size):
            result = read(fd, size)
            if result == data:
                target.write_bytes(data.replace(b'Search', b'Sought'))
            return result
        with patch.object(typescript_inputs.os, 'read', mutate):
            with self.assertRaisesRegex(Rejected, 'changed while'):
                typescript_inputs.collect(self.root)

    def test_json_replacement_during_read_is_refused(self):
        target = self.root / 'preview-features.json'
        data = target.read_bytes()
        read = os.read
        def replace(fd, size):
            result = read(fd, size)
            if result == data:
                target.rename(self.root / 'old-manifest')
                target.write_bytes(data)
            return result
        with patch.object(typescript_inputs.os, 'read', replace):
            with self.assertRaisesRegex(Rejected, 'changed while'):
                typescript_inputs.collect(self.root)

    def test_deep_json_is_a_refusal_not_a_parser_crash(self):
        self.put('preview-features.json', '{"features":' + '[' * 1100 + '0' + ']' * 1100 + ',"version":1}')
        with self.assertRaises(Rejected):
            typescript_inputs.collect(self.root)


class ConfigCacheTests(unittest.TestCase):
    def collect_memory(self, content):
        with patch.object(typescript_inputs, '_read', side_effect=lambda root, path, remaining: content[path]):
            return typescript_inputs.collect('/synthetic-config-root')

    def test_shared_parent_parsed_and_resolved_once_per_collection(self):
        paths = ['tsconfig.base.json'] + [f'tsconfig.leaf{i}.json' for i in range(128)]
        content = {'tsconfig.json': json.dumps({'files': ['src/a.ts'], 'dervoInputs': paths}).encode(),
                   'tsconfig.base.json': b'{"compilerOptions":{"strict":true}}' + b' ' * 4096}
        content.update({p: json.dumps({'extends': './tsconfig.base.json', 'compilerOptions': {'target': 'ES2020'}}).encode() for p in paths[1:]})
        parsed, merged = [], []
        validate = profiles.validate_tsconfig
        class CountedConfig(dict):
            def items(self):
                merged.append(self)
                return super().items()
        def counted(data, **kwargs):
            parsed.append(data)
            return CountedConfig(validate(data, **kwargs))
        with patch.object(profiles, 'validate_tsconfig', side_effect=counted):
            for collection in range(2):
                self.assertEqual(self.collect_memory(content), content)
                self.assertEqual(len(parsed), len(content) * (collection + 1))
                self.assertEqual(len(merged), len(content) * (collection + 1))

    def test_cached_parent_depth_boundary_and_cycle_safety(self):
        # List parents first so the later child reaches an already resolved chain.
        content = {'tsconfig.json': b'{"files":["src/a.ts"]}'}
        for i in range(32):
            config = {'compilerOptions': {'strict': True}} if i == 0 else {'extends': f'./tsconfig.c{i-1}.json'}
            content[f'tsconfig.c{i}.json'] = json.dumps(config).encode()
        with patch.object(profiles, 'validate_tsconfig', wraps=profiles.validate_tsconfig) as validate:
            effective = typescript_inputs.configs(content)
            self.assertEqual(effective['files'], ['src/a.ts'])
            self.assertEqual(validate.call_count, len(content))
        content['tsconfig.c32.json'] = b'{"extends":"./tsconfig.c31.json"}'
        with self.assertRaisesRegex(Rejected, 'depth cap'):
            typescript_inputs.configs(content)
        del content['tsconfig.c32.json']
        content['tsconfig.x.json'] = b'{"extends":"./tsconfig.y.json"}'
        content['tsconfig.y.json'] = b'{"extends":"./tsconfig.x.json"}'
        with self.assertRaisesRegex(Rejected, 'cycle'):
            typescript_inputs.configs(content)
        with self.assertRaisesRegex(Rejected, 'cycle'):
            typescript_inputs.configs({'tsconfig.json': b'{"extends":"./tsconfig.json"}'})

    def test_uncached_depth_and_inherited_options_remain_isolated(self):
        content = {'tsconfig.json': b'{"extends":"./tsconfig.c30.json","files":["src/a.ts"]}'}
        for i in range(31):
            config = {'compilerOptions': {'strict': True}} if i == 0 else {'extends': f'./tsconfig.c{i-1}.json'}
            content[f'tsconfig.c{i}.json'] = json.dumps(config).encode()
        self.assertTrue(typescript_inputs.configs(content)['compilerOptions']['strict'])
        content['tsconfig.c31.json'] = b'{"extends":"./tsconfig.c30.json"}'
        content['tsconfig.json'] = b'{"extends":"./tsconfig.c31.json","files":["src/a.ts"]}'
        with self.assertRaisesRegex(Rejected, 'depth cap'):
            typescript_inputs.configs(content)
        content = {'tsconfig.json': b'{"extends":"./tsconfig.base.json","compilerOptions":{"strict":false}}',
                   'tsconfig.base.json': b'{"files":["src/a.ts"],"compilerOptions":{"strict":true,"target":"ES2020"}}',
                   'tsconfig.sibling.json': b'{"extends":"./tsconfig.base.json","compilerOptions":{"target":"ES2022"}}'}
        effective = typescript_inputs.configs(content)
        self.assertEqual(effective['files'], ['src/a.ts'])
        self.assertEqual(effective['compilerOptions'], {'strict': False, 'target': 'ES2020'})

    def test_unused_configs_and_changed_bytes_still_validate(self):
        content = {'tsconfig.json': b'{"files":["src/a.ts"]}', 'tsconfig.base.json': b'{}'}
        typescript_inputs.configs(content)
        for data in (b'{"compilerOptions":{"noCheck":true}}', b'{"references":[]}',
                     b'{"extends":"./tsconfig.missing.json"}', b'{"extends":"../tsconfig.json"}', b'broken'):
            with self.subTest(data=data):
                content['tsconfig.base.json'] = data
                with self.assertRaises(Rejected):
                    typescript_inputs.configs(content)


FRONTEND = Path(__file__).resolve().parent.parent / 'frontend'
IMPORT = re.compile(r'''\b(?:from\s*|import\s*\(?\s*)["']([^"']+)["']''')
DECLARATION_IMPORT = re.compile(r'''\bfrom\s*["']([^"'\n]+)["']|\bimport\s*\(\s*["']([^"'\n]+)["']\s*\)''')
# A bare package specifier, so union types full of quoted CSS property names in a .d.ts are not read as imports.
PACKAGE = re.compile(r'(@[a-z0-9~][a-z0-9._~-]*/)?[a-z0-9~][a-z0-9._~-]*(/[A-Za-z0-9._~-]+)*')

# The roots of the representative selection. Everything else in it is derived: SEARCH_SOURCES below is the
# real import closure of these files, so a new import inside Dervo enlarges the selection instead of quietly
# leaving a hole in it. The closure has to stay inside the production task scope cap of 32 files, which is the
# bound this protects. When a root outgrows it the fixture names the files rather than failing inside tsc.
SELECTED_ROOTS = tuple('frontend/' + p for p in (
    'src/local/api.ts', 'src/local/collaboration.ts', 'src/shared/ui/input.tsx', 'src/shared/features/manifest.ts'))
NEGATIVE_CONTROL = 'frontend/src/local/FullAccess.tsx'   # a real Button consumer, so a wrong prop is a real TS2322

# WorkspaceSearch.tsx is in the task scope and is the file the evidence tests read, edit and discover, but it is
# deliberately outside the compiled selection: its own import closure is 54 source files and reaches shiki,
# motion and @tanstack/react-query, so it cannot be type-checked inside a 32-file scope. Keeping it scoped but
# unchecked is the honest shape, and the report says so.
SEARCH_SOURCE = 'frontend/src/local/WorkspaceSearch.tsx'


def selected_paths():
    """The frontend's own path aliases, rebased onto the selected app root."""
    config = json.loads(profiles._strip_json_comments((FRONTEND / 'tsconfig.json').read_text(encoding='utf-8')))
    return {alias: ['./' + posixpath.normpath(posixpath.join('frontend', p)) for p in paths]
            for alias, paths in config['compilerOptions']['paths'].items()}


def resolve_source(app, base, specifier, aliases):
    """The one project file a relative or aliased import names, or None for a package import.

    A lexical resolver for this fixture's literal imports, not TypeScript's. The confined compiler still
    reports anything this misses, which is what makes the derived selection checkable rather than assumed.
    """
    if specifier.startswith('.'):
        target = posixpath.join(posixpath.dirname(base), specifier)
    elif specifier.startswith('@/'):
        target = aliases['@/*'][0].replace('*', specifier[2:])
    elif specifier in aliases:
        target, = aliases[specifier]
    else:
        return None
    target = posixpath.normpath(target)
    # A .json import (resolveJsonModule is on) is a file the compiler reads too: the interface catalogs since beta.17.
    if target.endswith('.json'):
        return target if (app / target).is_file() else None
    for candidate in (target, target + '.ts', target + '.tsx', target + '/index.ts', target + '/index.tsx'):
        if candidate.endswith(('.ts', '.tsx')) and (app / candidate).is_file():
            return candidate
    return None


def source_closure(app, aliases, roots):
    seen, pending = set(), list(roots)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path.endswith('.json'):
            continue
        for specifier in IMPORT.findall((app / path).read_text(encoding='utf-8')):
            target = resolve_source(app, path, specifier, aliases)
            if target:
                pending.append(target)
    return tuple(sorted(seen))


CLOSURE = source_closure(FRONTEND.parent, selected_paths(), SELECTED_ROOTS)
# The compiled sources, and the JSON they import. The JSON travels as ordinary task files (the interface catalogs are
# about 300 KB each, under task_evidence.FILE_LIMIT) and reaches the compiler through those imports, not tsconfig files.
SEARCH_SOURCES = tuple(p for p in CLOSURE if not p.endswith('.json'))
SEARCH_DATA = tuple(p for p in CLOSURE if p.endswith('.json') and p.startswith('frontend/'))  # not preview-features.json, a read-only input
DATA_CAP = 1024 * 1024


def package_name(specifier):
    """The package a bare import names, without its subpath: ``@tauri-apps/api/core`` is ``@tauri-apps/api``."""
    parts = specifier.split('/')
    return '/'.join(parts[:2]) if specifier.startswith('@') else parts[0]


def package_imports(app, paths):
    aliases = selected_paths()
    names = set()
    for path in paths:
        for specifier in IMPORT.findall((app / path).read_text(encoding='utf-8')):
            if not specifier.startswith('.') and not specifier.startswith('@/') and specifier not in aliases:
                names.add(package_name(specifier))
    return sorted(names)


def declaration_imports(path):
    """Packages a declaration file itself imports. Declarations carry their own type dependencies, and a
    missing one does not fail as TS2307 under skipLibCheck: it silently widens props, so the closure is real
    only if it follows them."""
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return []
    names = set()
    for direct, dynamic in DECLARATION_IMPORT.findall(text):
        specifier = direct or dynamic
        if specifier and not specifier.startswith('.') and PACKAGE.fullmatch(specifier):
            names.add(package_name(specifier))
    return sorted(names)


def package_roots(name, modules):
    """Where a package's declarations install, as ``(name under node_modules, resolved folder)`` pairs.

    pnpm keeps a package's own dependencies in the node_modules beside it, so resolution looks there first and
    then at the project's own. Both the package and its @types are taken when both exist: ``react`` ships no
    declarations of its own, and the compiler only finds them under ``@types/react``.
    """
    typed = '@types/' + (name[1:].replace('/', '__') if name.startswith('@') else name)
    found = []
    for installed in (name, typed):
        for folder in (modules, FRONTEND / 'node_modules'):
            candidate = folder / installed
            if candidate.exists():
                found.append((installed, candidate.resolve(strict=True)))
                break
    return found


def search_fixture(root):
    """Coordinator-only cache preparation. No package JS imports or live cache read grants.

    This is an explicit representative frontend selection, not include expansion or
    project-reference equivalence. Return the complete derivation and copy provenance.
    """
    import posixpath
    app = FRONTEND.parent
    originals = {p: (app / p).read_bytes() for p in ('frontend/tsconfig.json', 'frontend/tsconfig.node.json')}
    config = json.loads(profiles._strip_json_comments(originals['frontend/tsconfig.json'].decode()))
    if set(config) != {'compilerOptions', 'include', 'references'} or config['include'] != ['src'] \
            or config['references'] != [{'path': './tsconfig.node.json'}]:
        raise AssertionError('Canonical frontend config changed; review the explicit selection derivation')
    expected = {'@/*': ['./src/*'], '@features-manifest': ['../preview-features.json'],
                '@protected-features': ['./src/protectedFeatures/public.ts'],
                '@protected-feature-components': ['./src/protectedFeatures/publicUi.ts'],
                '@model-capabilities-manifest': ['../scripts/model-capabilities.json'],
                'tweetnacl': ['./node_modules/tweetnacl']}  # the phone link's vendored build (1ff2ae9b); nothing selected imports it
    if config['compilerOptions']['paths'] != expected:
        raise AssertionError('Canonical aliases changed; review rebasing before copying')
    options = dict(config['compilerOptions'])
    options['paths'] = {alias: ['./' + posixpath.normpath(posixpath.join('frontend', p)) for p in paths]
                        for alias, paths in expected.items()}
    sources = {p: typescript_inputs._read(app, p, 262144) for p in SEARCH_SOURCES}
    sources.update({p: typescript_inputs._read(app, p, DATA_CAP) for p in SEARCH_DATA})
    sources[SEARCH_SOURCE] = typescript_inputs._read(app, SEARCH_SOURCE, 262144)
    if len(sources) + 1 > 32:
        raise AssertionError('Representative selection outgrew the 32-file task scope: '
                             + ', '.join(sorted(sources)))
    candidates, provenance, packages = {}, {}, set()
    examined = 0
    pending_packages = [(name, FRONTEND / 'node_modules') for name in package_imports(app, SEARCH_SOURCES)]
    while pending_packages:
        name, near = pending_packages.pop()
        if name in packages:
            continue
        packages.add(name)
        # Nothing is invented for a package that resolves nowhere: it fails visibly in the compiler's own TS2307.
        for installed, cached in package_roots(name, near):
            modules = cached.parents[installed.count('/')]
            pending = [cached]
            while pending:
                folder = pending.pop()
                with os.scandir(folder) as entries:
                    for entry in entries:
                        examined += 1
                        if examined > 8192:
                            raise AssertionError('Fixture package enumeration exceeded 8192 entries')
                        if entry.is_symlink():
                            raise AssertionError('Symlink below resolved fixture package: ' + entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name != 'node_modules' and not entry.name.startswith('.'):
                                pending.append(Path(entry.path))
                            continue
                        if entry.name == 'package.json' or typescript_inputs.DECLARATION.fullmatch(entry.name) \
                                or typescript_inputs.LICENSE.fullmatch(entry.name):
                            relative = Path(entry.path).relative_to(cached).as_posix()
                            path = 'node_modules/' + installed + '/' + relative
                            typescript_inputs._kind(path)
                            candidates[path] = (cached, relative, entry.stat(follow_symlinks=False).st_size)
                            if typescript_inputs.DECLARATION.fullmatch(entry.name):
                                for dependency in declaration_imports(Path(entry.path)):
                                    pending_packages.append((dependency, modules))
    total = sum(item[2] for item in candidates.values())
    if len(candidates) + 2 > typescript_inputs.FILE_CAP or total + 327680 > typescript_inputs.BYTE_CAP:
        raise AssertionError(f'Fixture declarations exceed existing caps: {len(candidates)} files, {total} bytes')
    inputs = {}
    for path, (cached, relative, size) in sorted(candidates.items()):
        data = typescript_inputs._read(cached, relative, typescript_inputs.SINGLE_CAP)
        if len(data) != size:
            raise AssertionError('Fixture input size changed during preparation: ' + path)
        inputs[path] = data
        provenance[path] = dict(source=str(cached / relative), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    inputs['preview-features.json'] = typescript_inputs._read(app, 'preview-features.json', typescript_inputs.PREVIEW_CAP)
    for path, data in {**sources, **inputs}.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    install_compiler(root)
    selected = {'compilerOptions': options, 'files': list(SEARCH_SOURCES), 'dervoInputs': sorted(inputs)}
    (root / 'tsconfig.json').write_text(json.dumps(selected, indent=2) + '\n')
    report = dict(
        scope='Derived import closure of the real local API surface and the preview-manifest consumer',
        original_configs={p: dict(text=data.decode(), sha256=hashlib.sha256(data).hexdigest()) for p, data in originals.items()},
        selected_config=selected,
        transformations=[dict(option='compilerOptions.paths', before=expected, after=options['paths'], reason='Rebase from frontend to selected app root'),
                         dict(option='include', before=['src'], after=None, reason='Explicit representative source selection, not whole frontend'),
                         dict(option='files', before=None, after=list(SEARCH_SOURCES), reason='Bound the real source obligations'),
                         dict(option='data', before=None, after=list(SEARCH_DATA), reason='JSON the sources import, copied as task files'),
                         dict(option='references', before=config['references'], after=None, reason='Separate build-tool project remains unchecked'),
                         dict(option='dervoInputs', before=None, after=sorted(inputs), reason='Explicit copied read-only inputs')],
        unchanged_options={k: v for k, v in options.items() if k != 'paths'},
        unsupported=['Whole frontend include scope', 'frontend/tsconfig.node.json and vite.config.ts build-tool project',
                     'scripts/model-capabilities.json alias target is rebased but not copied or checked',
                     'Unused protected-feature-components alias target is not copied or checked',
                     SEARCH_SOURCE + ' is in the task scope but outside the compiled selection: its own import '
                     'closure is 54 source files and reaches shiki, motion and @tanstack/react-query, past the '
                     '32-file scope a task can hold'],
        source_files={p: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for p, data in sources.items()},
        package_provenance=provenance, package_entries_examined=examined, packages=sorted(packages),
        source_count=len(sources), source_bytes=sum(map(len, sources.values())),
        dependency_count=len(inputs), dependency_bytes=sum(map(len, inputs.values())))
    return list(SEARCH_SOURCES) + list(SEARCH_DATA) + [SEARCH_SOURCE, 'tsconfig.json'], report


def announced_busy_change(data):
    """Disposable improvement inside the compiled selection: the confirm button announces that it is working.

    The label already changes to "Turning on…", but nothing tells a screen reader, and ``aria-busy`` is a
    typed prop, so the compiler has something real to accept or reject.
    """
    old = b'<Button size="sm" variant="destructive" disabled={busy} onClick={()=>void set(true)}>'
    new = b'<Button size="sm" variant="destructive" disabled={busy} aria-busy={busy} onClick={()=>void set(true)}>'
    if data.count(old) != 1:
        raise AssertionError('FullAccess confirm button changed; review the proposed edit')
    return data.replace(old, new)


def retained_query_change(data):
    """Disposable improvement: an empty query removes its retained storage entry."""
    old = b'try{sessionStorage.setItem(key,value);}catch'
    new = b'try{if(value)sessionStorage.setItem(key,value);else sessionStorage.removeItem(key);}catch'
    if data.count(old) != 1:
        raise AssertionError('WorkspaceSearch retention source changed; review the proposed edit')
    return data.replace(old, new)


def retention_probe_source(data):
    """Exercise the real change() body with synthetic storage, without importing React.

    This tests only storage and in-memory retention, not rendering or navigation.
    """
    text = data.decode()
    body = text.split('  function change(value:string){\n', 1)[1].split('\n  }\n  async function run', 1)[0]
    return ("import assert from 'node:assert/strict';\nimport test from 'node:test';\n"
            "test('query retention and clear', () => {\n"
            "const key='synthetic-project'; let queries={}; const saved=new Map();\n"
            "const setQueries=fn=>{queries=fn(queries)};\n"
            "const sessionStorage={setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};\n"
            "function change(value){\n" + body + "\n}\n"
            "change('normal search'); assert.equal(saved.get(key),'normal search');\n"
            "assert.equal(queries[key],'normal search');\n"
            "change(''); assert.equal(queries[key],''); assert.equal(saved.has(key),false);\n"
            "sessionStorage.setItem=()=>{throw Error('storage unavailable')};\n"
            "change('retained in memory'); assert.equal(queries[key],'retained in memory');\n"
            "});\n").encode()


DERVO_SOURCES = ('src/shared/ui/button.tsx', 'src/shared/ui/badge.tsx',
                 'src/shared/ui/skeleton.tsx', 'src/shared/lib/cn.ts')


def copy_cached_package(root, name, source=None):
    """Tests alone resolve the cache. Production only reads explicit in-root files."""
    source = (source or FRONTEND / 'node_modules' / name).resolve(strict=True)
    paths = []
    for folder, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d != 'node_modules' and not d.startswith('.')]
        for filename in files:
            if filename == 'package.json' or typescript_inputs.DECLARATION.fullmatch(filename) or typescript_inputs.LICENSE.fullmatch(filename):
                relative = (Path(folder) / filename).relative_to(source)
                path = 'node_modules/' + name + '/' + relative.as_posix()
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
                paths.append(path)
    return sorted(paths)


def react_fixture(root):
    install_compiler(root)
    packages = ['@types/react', '@radix-ui/react-slot', '@radix-ui/react-compose-refs',
                'class-variance-authority', 'clsx', 'tailwind-merge']
    inputs = []
    for name in packages:
        source = FRONTEND / 'node_modules' / name
        if not source.exists() and name == '@radix-ui/react-compose-refs':
            slot = (FRONTEND / 'node_modules/@radix-ui/react-slot').resolve()
            source = slot.parent / 'react-compose-refs'
        inputs.extend(copy_cached_package(root, name, source))
    react = (FRONTEND / 'node_modules/@types/react').resolve()
    inputs.extend(copy_cached_package(root, 'csstype', react.parent.parent / 'csstype'))
    inputs.append('tsconfig.base.json')
    (root / 'tsconfig.base.json').write_text(json.dumps({'compilerOptions': {
        'target': 'ES2020', 'lib': ['ES2020', 'DOM', 'DOM.Iterable'], 'module': 'ESNext',
        'moduleResolution': 'bundler', 'jsx': 'react-jsx', 'strict': True, 'noEmit': True,
        'types': ['react'], 'paths': {'@/*': ['./src/*']}}}))
    for path in DERVO_SOURCES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FRONTEND / path, target)
    (root / 'src/consumer.tsx').write_text('import {Button} from "./shared/ui/button";\nexport const sample = <Button disabled={false} variant="outline">Dervo</Button>;\n')
    (root / 'tsconfig.json').write_text(json.dumps({'extends': './tsconfig.base.json',
        'files': [*DERVO_SOURCES, 'src/consumer.tsx'], 'dervoInputs': sorted(inputs)}))
    return [*DERVO_SOURCES, 'src/consumer.tsx', 'tsconfig.json']


class SearchTests(unittest.TestCase):
    def setUp(self):
        if not cached_compiler() or not node_available():
            self.skipTest('Cached TypeScript compiler or trusted Node unavailable')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'selected-app'
        self.root.mkdir()
        self.sources, self.report = search_fixture(self.root)

    def service(self, *, discover=False):
        from dervo_connections import Store
        store = Store(self.base / 'synthetic.sqlite')
        self.addCleanup(store.close)
        store.add_profile('p', 'codex', self.base / 'synthetic-identity', 'synthetic@example.test', 'personal')
        store.add_project('p', self.root, 'p', 'synthetic-model')
        store.add_thread('c', 'p', 'c', 'private', 'local-user')
        ctx = dict(thread_id='c', project_id='p', actor='local-user')
        service = task_evidence.TaskEvidence(store)
        files = [p for p in self.sources if not discover or p != SEARCH_SOURCE]
        task = service.create(files=files, folders=['frontend/src/local'] if discover else [],
                              test_file='tsconfig.json', check_profile=profiles.TYPESCRIPT, **ctx)
        service.approve(task['id'], scope_version=1, **ctx)
        return service, task, ctx

    def test_real_selection_and_all_config_transformations_are_explicit(self):
        self.assertEqual(self.report['source_count'], len(SEARCH_SOURCES) + len(SEARCH_DATA) + 1)
        self.assertLessEqual(len(self.sources), 32, 'the scope has to fit what a task can hold')
        self.assertEqual(self.report['selected_config']['files'], list(SEARCH_SOURCES))
        self.assertNotIn(SEARCH_SOURCE, self.report['selected_config']['files'])
        self.assertTrue(any(SEARCH_SOURCE in item for item in self.report['unsupported']),
                        'a scoped file left out of the compiled selection has to say so')
        for path in (*SEARCH_SOURCES, *SEARCH_DATA, SEARCH_SOURCE):
            self.assertEqual((self.root / path).read_bytes(), (FRONTEND.parent / path).read_bytes())
        original = json.loads(profiles._strip_json_comments(self.report['original_configs']['frontend/tsconfig.json']['text']))
        selected = self.report['selected_config']
        self.assertEqual({k: v for k, v in original['compilerOptions'].items() if k != 'paths'}, self.report['unchanged_options'])
        self.assertEqual(selected['compilerOptions']['paths']['@features-manifest'], ['./preview-features.json'])
        self.assertNotIn('references', selected)
        self.assertTrue(any('build-tool' in item for item in self.report['unsupported']))
        content = profiles.typescript_snapshot(self.root)['content']
        content.update({p: (self.root / p).read_bytes() for p in self.sources})
        typescript_inputs.validate_snapshot(content)
        for name in ('LICENSE_MIT', 'LICENSE_APACHE-2.0'):
            self.assertIn('node_modules/@tauri-apps/api/' + name, content)

    def test_selected_local_imports_and_reexports_have_real_source_closure(self):
        # A lexical guard for this fixture's literal imports, not a TS resolver.
        # The confined compiler still checks resolution and all source diagnostics.
        available = set(self.sources) | set(self.report['selected_config']['dervoInputs'])
        aliases = self.report['selected_config']['compilerOptions']['paths']
        edges = []
        for path in SEARCH_SOURCES:
            text = (self.root / path).read_text(encoding='utf-8')
            for specifier in re.findall(r'''\b(?:from\s*|import\s*\(?\s*)["']([^"']+)["']''', text):
                if specifier.startswith('.'):
                    target = posixpath.join(posixpath.dirname(path), specifier)
                elif specifier.startswith('@/'):
                    target = aliases['@/*'][0].replace('*', specifier[2:])
                elif specifier in aliases:
                    target, = aliases[specifier]
                else:
                    continue
                target = posixpath.normpath(target)
                candidates = {target, target + '.ts', target + '.tsx',
                              target + '/index.ts', target + '/index.tsx'}
                matches = candidates & available
                self.assertTrue(matches, f'{path}: missing explicit source for {specifier}')
                for match in matches:
                    self.assertEqual((self.root / match).read_bytes(),
                                     (FRONTEND.parent / match).read_bytes())
                    edges.append((path, match))
        self.assertIn(('frontend/src/shared/api/personaTypes.ts',
                       'frontend/src/shared/api/types.ts'), edges)
        self.assertGreater(len(edges), len(SEARCH_SOURCES), 'the selection has to be a closure, not a list of leaves')

    def test_read_before_write_and_write_before_read_preserve_complete_snapshots(self):
        # Recreate the synthetic service between orders to avoid attempt history sharing.
        for read_first in (True, False):
            with self.subTest(read_first=read_first):
                service, task, ctx = self.service(discover=True)
                target = self.root / SEARCH_SOURCE
                before = target.read_bytes()
                after = retained_query_change(before)
                if read_first:
                    self.assertEqual(base64.b64decode(service.read_file(task['id'], SEARCH_SOURCE, **ctx)['bytes_b64']), before)
                attempt = service.prepare(task['id'], changes={SEARCH_SOURCE: {
                    'expected_sha256': hashlib.sha256(before).hexdigest(), 'content': after}}, **ctx)
                read = service.read_file(task['id'], SEARCH_SOURCE, **ctx)
                self.assertEqual(base64.b64decode(read['bytes_b64']), after)
                self.assertEqual(set(attempt['before']), set(self.sources))
                self.assertEqual(set(attempt['after']), set(self.sources))
                self.assertEqual(base64.b64decode(attempt['before'][SEARCH_SOURCE]['bytes_b64']), before)
                self.assertEqual(attempt['after'][SEARCH_SOURCE], read)
                for path in set(self.sources) - {SEARCH_SOURCE}:
                    self.assertEqual(attempt['before'][path], attempt['after'][path])
                self.assertTrue(service._current(service.read(task['id'], **ctx), attempt))
                target.write_bytes(before)
                service.store.close()
                (self.base / 'synthetic.sqlite').unlink()

    def test_json_hash_mutation_refuses_launch_and_review(self):
        service, task, ctx = self.service()
        attempt = service.verify_current(task['id'], **ctx)
        target = self.root / 'preview-features.json'
        original = target.read_bytes()
        target.write_bytes(original.replace(b'Workflows', b'WorkflowX'))
        with patch.object(task_evidence.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
            with self.assertRaisesRegex(Rejected, 'compiler changed'):
                service.launch(task['id'], attempt_number=attempt['number'], **ctx)
        self.assertFalse(service._current(task, attempt))
        target.write_bytes(original)
        self.assertTrue(service._current(task, attempt))

    def test_bound_read_only_json_cannot_be_written_by_task(self):
        service, task, ctx = self.service()
        service.scope(task['id'], files=self.sources + ['preview-features.json'], test_file='tsconfig.json', **ctx)
        service.approve(task['id'], scope_version=2, **ctx)
        target = self.root / 'preview-features.json'
        original = target.read_bytes()
        with self.assertRaisesRegex(Rejected, 'Read-only TypeScript input'):
            service.prepare(task['id'], changes={'preview-features.json': {
                'expected_sha256': hashlib.sha256(original).hexdigest(), 'content': original.replace(b'Workflows', b'WorkflowX')}}, **ctx)
        self.assertEqual(target.read_bytes(), original)

    def test_immutable_transport_after_selected_root_is_removed(self):
        record = profiles.typescript_snapshot(self.root)
        compiler = dict(files=record.pop('content'), identity=record)
        snapshots = {p: dict(bytes_b64=base64.b64encode((self.root / p).read_bytes()).decode()) for p in self.sources}
        argv = profiles.argv_for(profiles.TYPESCRIPT, 'tsconfig.json')
        shutil.rmtree(self.root)
        calls = []
        def confined(root, command, **kwargs):
            calls.append(command)
            if command == argv:
                self.assertEqual((root / SEARCH_SOURCE).read_bytes(), base64.b64decode(snapshots[SEARCH_SOURCE]['bytes_b64']))
                self.assertEqual((root / 'preview-features.json').read_bytes(), compiler['files']['preview-features.json'])
                self.assertEqual(set(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()), set(compiler['files']) | set(snapshots))
            return dict(exit_status=0, stopped=None, stdout='host-canaries-denied', stderr='')
        with patch.object(task_evidence, '_run_confined', confined):
            task_evidence.TaskEvidence._verify(dict(argv=argv), snapshots, compiler=compiler)
        self.assertEqual(len(calls), 2)

    def test_real_confined_search_before_after_and_negative_control(self):
        record = profiles.typescript_snapshot(self.root)
        compiler = dict(files=record.pop('content'), identity=record)
        task = dict(argv=profiles.argv_for(profiles.TYPESCRIPT, 'tsconfig.json'))
        def run():
            snapshots = {p: dict(bytes_b64=base64.b64encode((self.root / p).read_bytes()).decode()) for p in self.sources}
            return task_evidence.TaskEvidence._verify(task, snapshots, compiler=compiler)
        try:
            before = run()
        except task_evidence.ConfinementBlocked as exc:
            self.skipTest(str(exc))
        self.assertEqual(before['exit_status'], 0, before)
        self.assertIsNone(before['stopped'], before)
        # The proposed edit and the wrong type both land on a file inside the compiled selection, or neither
        # result would say anything about the check. Editing a scoped-but-unchecked file proves nothing.
        target = self.root / NEGATIVE_CONTROL
        self.assertIn(NEGATIVE_CONTROL, SEARCH_SOURCES)
        target.write_bytes(announced_busy_change(target.read_bytes()))
        after = run()
        self.assertEqual(after['exit_status'], 0, after)
        self.assertIsNone(after['stopped'], after)
        good = target.read_bytes()
        self.assertIn(b'disabled={busy}', good)
        target.write_bytes(good.replace(b'disabled={busy}', b'disabled="wrong"', 1))
        bad = run()
        self.assertNotEqual(bad['exit_status'], 0, bad)
        self.assertIsNone(bad['stopped'], bad)
        self.assertIn('TS2322', bad['stdout'] + bad['stderr'])

    def test_real_confined_retention_behavior_red_then_green(self):
        task = dict(argv=profiles.argv_for(profiles.NODE, 'test_retention.mjs'))
        original = (self.root / SEARCH_SOURCE).read_bytes()
        def run(data):
            source = retention_probe_source(data)
            return task_evidence.TaskEvidence._verify(task, {'test_retention.mjs': {
                'bytes_b64': base64.b64encode(source).decode()}})
        try:
            before = run(original)
        except task_evidence.ConfinementBlocked as exc:
            self.skipTest(str(exc))
        self.assertNotEqual(before['exit_status'], 0, before)
        self.assertIsNone(before['stopped'], before)
        self.assertIn('ERR_ASSERTION', before['stdout'] + before['stderr'])
        after = run(retained_query_change(original))
        self.assertEqual(after['exit_status'], 0, after)
        self.assertIsNone(after['stopped'], after)


class ReactTests(unittest.TestCase):
    def setUp(self):
        if not cached_compiler() or not node_available():
            self.skipTest('Cached TypeScript compiler or trusted Node unavailable')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sources = react_fixture(self.root)

    def test_actual_dervo_fixture_has_real_licensed_declaration_closure(self):
        record = profiles.typescript_snapshot(self.root)
        self.assertEqual(record['version'], '6.0.3')
        self.assertIn('node_modules/@types/react/index.d.ts', record['sha256'])
        self.assertIn('node_modules/csstype/index.d.ts', record['sha256'])
        self.assertIn(b'ButtonHTMLAttributes', record['content']['node_modules/@types/react/index.d.ts'])
        for path in DERVO_SOURCES:
            self.assertEqual((self.root / path).read_bytes(), (FRONTEND / path).read_bytes())
        content = dict(record['content'])
        content.update({p: (self.root / p).read_bytes() for p in self.sources})
        typescript_inputs.validate_snapshot(content)
        self.assertEqual(set(record['sha256']), set(record['files']))

    def test_worker_uses_only_frozen_bytes_and_keeps_cancellation(self):
        import threading
        record = profiles.typescript_snapshot(self.root)
        compiler = dict(files=record.pop('content'), identity=record)
        snapshots = {p: dict(bytes_b64=base64.b64encode((self.root / p).read_bytes()).decode()) for p in self.sources}
        argv = profiles.argv_for(profiles.TYPESCRIPT, 'tsconfig.json')
        cancel = threading.Event()
        # Remove the live fixture before the worker runs. This unit check tests
        # immutable transport only; it doesn't claim compiler or sandbox success.
        shutil.rmtree(self.root)
        seen = []
        def confined(root, command, **kwargs):
            self.assertIs(kwargs['cancel'], cancel)
            seen.append(command)
            if command == argv:
                self.assertEqual((root / 'node_modules/@types/react/index.d.ts').read_bytes(), compiler['files']['node_modules/@types/react/index.d.ts'])
                self.assertEqual(set(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()), set(compiler['files']) | set(snapshots))
                self.assertEqual((root / 'src/shared/ui/button.tsx').read_bytes(), base64.b64decode(snapshots['src/shared/ui/button.tsx']['bytes_b64']))
            return dict(exit_status=0, stopped=None, stdout='host-canaries-denied', stderr='')
        with patch.object(task_evidence, '_run_confined', confined):
            task_evidence.TaskEvidence._verify(dict(argv=argv), snapshots, cancel=cancel, compiler=compiler)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[-1], argv)

    def test_real_confined_react_check_and_wrong_prop_diagnostics(self):
        record = profiles.typescript_snapshot(self.root)
        compiler = dict(files=record.pop('content'), identity=record)
        task = dict(argv=profiles.argv_for(profiles.TYPESCRIPT, 'tsconfig.json'))
        def run():
            snapshots = {p: dict(bytes_b64=base64.b64encode((self.root / p).read_bytes()).decode()) for p in self.sources}
            return task_evidence.TaskEvidence._verify(task, snapshots, compiler=compiler)
        try:
            good = run()
        except task_evidence.ConfinementBlocked as exc:
            self.skipTest(str(exc))
        self.assertEqual(good['exit_status'], 0, good)
        self.assertIsNone(good['stopped'], good)
        target = self.root / 'src/consumer.tsx'
        target.write_text(target.read_text(encoding='utf-8').replace('disabled={false}', 'disabled="wrong"'), encoding='utf-8', newline='')
        bad = run()
        self.assertNotEqual(bad['exit_status'], 0, bad)
        self.assertIn('TS2322', bad['stdout'] + bad['stderr'])
        self.assertNotIn('TS2307', good['stdout'] + good['stderr'])

    def test_dependency_and_imported_config_mutation_invalidate_launch_and_review(self):
        from dervo_connections import Store
        store = Store(self.root / 'synthetic.sqlite')
        self.addCleanup(store.close)
        store.add_profile('p', 'codex', self.root / 'identity', 'synthetic@example.test', 'personal')
        store.add_project('p', self.root, 'p', 'synthetic-model')
        store.add_thread('c', 'p', 'c', 'private', 'local-user')
        ctx = dict(thread_id='c', project_id='p', actor='local-user')
        service = task_evidence.TaskEvidence(store)
        task = service.create(files=self.sources, test_file='tsconfig.json', check_profile=profiles.TYPESCRIPT, **ctx)
        service.approve(task['id'], scope_version=1, **ctx)
        attempt = service.verify_current(task['id'], **ctx)
        self.assertTrue(service._current(task, attempt))
        for path in ('node_modules/@types/react/index.d.ts', 'tsconfig.base.json'):
            target = self.root / path
            original = target.read_bytes()
            target.write_bytes(original.replace(b'HTMLAttributes', b'HTMXAttributes', 1) if path.endswith('.d.ts') else original.replace(b'ES2020', b'ES2021', 1))
            with patch.object(task_evidence.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
                with self.assertRaisesRegex(Rejected, 'compiler changed'):
                    service.launch(task['id'], attempt_number=attempt['number'], **ctx)
            self.assertFalse(service._current(task, attempt))
            target.write_bytes(original)
            self.assertTrue(service._current(task, attempt))
