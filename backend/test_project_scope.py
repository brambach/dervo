"""Scoped-project capability: explicit granted folders allow bounded listing, literal search and absent-before creation
through the production broker, both provider adapters and the existing project permission. Real Host, Store, broker
and evidence; providers are synthetic peers. Nothing here launches a provider, reads credentials, touches the network
or leaves the temporary project.
"""
import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from host import Host, ACTOR
from dervo_connections import Rejected
from task_evidence import TaskEvidence

SAMPLE = 'import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n'
CTX = dict(thread_id='c', project_id='p', actor=ACTOR)


class ProjectScopeBase(unittest.TestCase):
    provider = 'codex'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.host = Host(self.base / 'store')
        self.host_closed = False
        self.addCleanup(self.close_host)
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text(SAMPLE)
        (self.project / 'greeting.py').write_text('def greet(): return "hi"\n')
        src = self.project / 'src'
        (src / 'pkg' / 'deep').mkdir(parents=True)
        (src / 'app.py').write_text('def main():\n    return greet()\n')
        (src / 'pkg' / 'util.py').write_text('NEEDLE = 1\n')
        (src / 'pkg' / 'deep' / 'leaf.py').write_text('# NEEDLE here too\n')
        (src / '.env').write_text('SECRET=1\n')
        (src / 'credentials.json').write_text('{"NEEDLE": true}\n')
        os.symlink(self.project / 'greeting.py', src / 'link.py')
        (self.project / 'hard_origin.py').write_text('NEEDLE hardlinked\n')
        os.link(self.project / 'hard_origin.py', src / 'hard.py')
        (self.project / 'sibling').mkdir()
        (self.project / 'sibling' / 'other.py').write_text('NEEDLE sibling\n')
        os.symlink(self.project / 'sibling', src / 'escape')
        (self.project / 'docs').mkdir()
        self.identity = 'personal@example.test' if self.provider == 'claude' else 'synthetic@example.test'
        self.host.store.add_profile('p', self.provider, self.base / 'store/profiles/p', self.identity, 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.scope = dict(projectId='p', channelId='c')
        self.evidence = TaskEvidence(self.host.store)

    def close_host(self):
        if not self.host_closed:
            self.host_closed = True
            self.host.close()

    def task(self, folders=('src',), files=('greeting.py', 'test_sample.py'), approve=True):
        task = self.evidence.create(**CTX, files=list(files), test_file='test_sample.py', folders=list(folders))
        if approve:
            self.evidence.approve(task['id'], scope_version=1, **CTX)
        return task['id']


class BrokerTests(ProjectScopeBase):
    """The evidence broker alone: descriptor-based folder safety, bounds and creation evidence."""

    def test_folder_grant_is_explicit_existing_and_never_the_root(self):
        for folders in (['.'], [''], ['../project'], ['src/..'], ['missing'], ['src/link.py'], ['src/escape'], ['.git'],
                        ['credentials'], ['src'] * 2, ['src/.env'], [f'd{i}' for i in range(9)]):
            with self.subTest(folders=folders):
                with self.assertRaises(Rejected):
                    self.task(folders)
        task = self.evidence.read(self.task(['src', 'docs']), **CTX)
        self.assertEqual(task['folders'], ['docs', 'src'])
        self.assertEqual(task['created'], [])
        legacy = self.evidence.read(self.task([]), **CTX)
        self.assertEqual(legacy['folders'], [], 'no default grant')

    def test_listing_is_bounded_nested_and_skips_links_secrets_and_dot_entries(self):
        task_id = self.task()
        listing = self.evidence.list_files(task_id, 'src', **CTX)
        self.assertEqual([(e['name'], e['kind']) for e in listing['entries']],
                         [('app.py', 'file'), ('pkg', 'directory')], listing)
        self.assertFalse(listing['truncated'])
        nested = self.evidence.list_files(task_id, 'src/pkg/deep', **CTX)
        self.assertEqual([e['name'] for e in nested['entries']], ['leaf.py'])
        for directory in ('.', '', 'sibling', 'src/escape', 'src/../sibling', 'docs', 'src/link.py', 'src/missing', '/src'):
            with self.subTest(directory=directory):
                with self.assertRaises(Rejected):
                    self.evidence.list_files(task_id, directory, **CTX)
        for i in range(TaskEvidence.LIST_LIMIT + 5):
            (self.project / 'src' / 'pkg' / f'gen_{i:04d}.py').write_text('x\n')
        big = self.evidence.list_files(task_id, 'src/pkg', **CTX)
        self.assertEqual(len(big['entries']), TaskEvidence.LIST_LIMIT)
        self.assertTrue(big['truncated'])

    def test_protected_store_and_profile_roots_are_never_granted(self):
        (self.host.root / 'test_sample.py').write_text(SAMPLE)
        (self.host.root / 'src').mkdir(exist_ok=True)
        self.host.store.add_project('protected', self.host.root, 'p', 'synthetic-model')
        self.host.store.add_thread('protected', 'protected', 'protected', 'private', ACTOR)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_create', {'projectId': 'protected', 'channelId': 'protected', 'text': 'x',
                                               'files': ['test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['src']})

    def test_search_is_literal_nested_bounded_and_truthful_about_scanning(self):
        task_id = self.task()
        found = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertEqual([(h['path'], h['line']) for h in found['hits']], [('src/pkg/util.py', 1), ('src/pkg/deep/leaf.py', 1)], found)
        self.assertFalse(found['truncated'])
        self.assertEqual(found['scanned']['files'], 3, 'app.py, util.py and leaf.py; links, hardlinks, dot and credential files are never opened')
        self.assertEqual(self.evidence.search_files(task_id, 'src', 'NEE.LE', **CTX)['hits'], [], 'literal substring, not a regex')
        with self.assertRaises(Rejected):
            self.evidence.search_files(task_id, 'sibling', 'NEEDLE', **CTX)
        with self.assertRaises(Rejected):
            self.evidence.search_files(task_id, 'src', '', **CTX)
        with self.assertRaises(Rejected):
            self.evidence.search_files(task_id, 'src', 'x' * 257, **CTX)
        for i in range(TaskEvidence.SEARCH_HITS + 10):
            (self.project / 'src' / f'hit_{i:03d}.py').write_text('NEEDLE\n')
        many = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertEqual(len(many['hits']), TaskEvidence.SEARCH_HITS)
        self.assertTrue(many['truncated'])
        with patch.object(TaskEvidence, 'SEARCH_FILES', 2):
            capped = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertTrue(capped['truncated'])
        self.assertEqual(capped['scanned']['files'], 2)

    def test_creation_succeeds_only_inside_scope_with_absent_before_evidence(self):
        task_id = self.task()
        attempt = self.evidence.create_file(task_id, 'src/pkg/new_module.py', b'VALUE = 2\n', **CTX)
        self.assertEqual((attempt['kind'], attempt['state'], attempt['created']), ('create', 'prepared', 'src/pkg/new_module.py'))
        self.assertIsNone(attempt['before']['src/pkg/new_module.py'], 'absent before')
        self.assertEqual(base64.b64decode(attempt['after']['src/pkg/new_module.py']['bytes_b64']), b'VALUE = 2\n')
        self.assertIn('+VALUE = 2', attempt['diff']['src/pkg/new_module.py'])
        self.assertTrue(attempt['diff']['src/pkg/new_module.py'].startswith('--- absent/src/pkg/new_module.py'))
        self.assertEqual(attempt['diff']['greeting.py'], '', 'granted existing files are snapshotted and unchanged')
        self.assertEqual((self.project / 'src/pkg/new_module.py').read_bytes(), b'VALUE = 2\n')
        task = self.evidence.read(task_id, **CTX)
        self.assertEqual(task['created'], ['src/pkg/new_module.py'])
        self.assertEqual(task['files'], ['greeting.py', 'test_sample.py'], 'the file grant itself is unchanged')
        self.assertTrue(self.evidence._current(task, attempt))
        # The created file is now readable and writable with the ordinary hash-checked contract, and stays in evidence.
        snap = self.evidence.read_file(task_id, 'src/pkg/new_module.py', **CTX)
        changed = self.evidence.prepare(task_id, changes={'src/pkg/new_module.py': dict(expected_sha256=snap['sha256'], content=b'VALUE = 3\n')}, **CTX)
        self.assertIn('src/pkg/new_module.py', changed['after'])
        self.assertIn('-VALUE = 2', changed['diff']['src/pkg/new_module.py'])

    def test_creation_conflicts_and_denials(self):
        task_id = self.task()
        cases = {'existing target': 'src/app.py', 'granted existing file': 'greeting.py', 'project root': 'brand_new.py',
                 'sibling folder': 'sibling/new.py', 'missing parent': 'src/nope/new.py', 'symlink escape': 'src/escape/new.py',
                 'symlink target': 'src/link.py', 'traversal': 'src/../new.py', 'dot file': 'src/.hidden.py',
                 'secret name': 'src/credentials.py', 'protected name': 'src/auth_token.py'}
        for label, path in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(Rejected):
                    self.evidence.create_file(task_id, path, b'x\n', **CTX)
        self.assertFalse((self.project / 'sibling' / 'new.py').exists())
        self.assertFalse((self.project / 'src' / 'nope').exists(), 'missing parents are refused, never created')
        self.assertEqual((self.project / 'src' / 'app.py').read_text(), 'def main():\n    return greet()\n', 'an existing target is never overwritten')
        self.assertEqual(self.evidence.read(task_id, **CTX)['attempts'], [], 'a refusal records no attempt and no created file')
        with self.assertRaises(Rejected):
            self.evidence.create_file(task_id, 'src/big.py', b'x' * 262145, **CTX)
        with self.assertRaises(Rejected):
            self.evidence.create_file(self.task([]), 'src/new.py', b'x\n', **CTX)  # file-only task: no creation authority
        unapproved = self.task(approve=False)
        with self.assertRaises(Rejected):
            self.evidence.create_file(unapproved, 'src/new.py', b'x\n', **CTX)

    def test_interrupted_creation_stays_uncertain_and_is_never_replayed(self):
        task_id = self.task()
        real_open = os.open

        def crash(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                raise KeyboardInterrupt()
            return real_open(path, flags, *args, **kwargs)
        with patch('task_evidence.os.open', crash):
            with self.assertRaises(KeyboardInterrupt):
                self.evidence.create_file(task_id, 'src/interrupted.py', b'x\n', **CTX)
        task = self.evidence.read(task_id, **CTX)
        self.assertEqual(task['attempts'][-1]['state'], 'uncertain')
        self.assertEqual(task['attempts'][-1]['kind'], 'create')
        self.assertIsNone(task['attempts'][-1]['after'])
        for call in (lambda: self.evidence.create_file(task_id, 'src/interrupted.py', b'x\n', **CTX),
                     lambda: self.evidence.create_file(task_id, 'src/other.py', b'x\n', **CTX),
                     lambda: self.evidence.verify_current(task_id, **CTX)):
            with self.assertRaises(Rejected):
                call()
        # A running creation attempt at restart is marked uncertain, like any other running attempt.
        with self.host.store.db:
            task['attempts'][-1]['state'] = 'running'
            self.host.store.db.execute('UPDATE task_evidence SET document=? WHERE id=?', (json.dumps(task), task_id))
        reopened = TaskEvidence(self.host.store).read(task_id, **CTX)
        self.assertEqual(reopened['attempts'][-1]['state'], 'uncertain')
        self.assertIn('restarted', reopened['attempts'][-1]['blocker'])

    def test_folder_grant_admits_discovered_regular_files_under_one_shared_policy(self):
        """List, search, read and write share one access policy: a regular file discovered under a granted folder can be
        read and edited with the ordinary hash-checked contract, and search opens nothing that read refuses."""
        task_id = self.task()
        snap = self.evidence.read_file(task_id, 'src/app.py', **CTX)
        self.assertEqual(base64.b64decode(snap['bytes_b64']), b'def main():\n    return greet()\n')
        task = self.evidence.read(task_id, **CTX)
        self.assertEqual(task['admitted'], ['src/app.py'], 'a read discovered file is durably admitted into evidence')
        self.assertEqual(task['files'], ['greeting.py', 'test_sample.py'], 'the file grant itself is unchanged')
        changed = self.evidence.prepare(task_id, changes={'src/pkg/util.py': dict(expected_sha256=hashlib.sha256(b'NEEDLE = 1\n').hexdigest(),
                                                                                  content=b'NEEDLE = 2\n')}, **CTX)
        self.assertEqual(changed['files'], ['greeting.py', 'test_sample.py', 'src/app.py', 'src/pkg/util.py'])
        self.assertIn('-NEEDLE = 1', changed['diff']['src/pkg/util.py'])
        self.assertEqual((self.project / 'src/pkg/util.py').read_bytes(), b'NEEDLE = 2\n')
        with self.assertRaises(Rejected):
            self.evidence.prepare(task_id, changes={'src/pkg/deep/leaf.py': dict(expected_sha256='0' * 64, content=b'x\n')}, **CTX)
        self.assertEqual(self.evidence.read(task_id, **CTX)['admitted'], ['src/app.py', 'src/pkg/util.py'], 'a stale preimage admits nothing new')
        verified = self.evidence.verify_current(task_id, **CTX)
        self.assertEqual(sorted(verified['after']), sorted(['greeting.py', 'test_sample.py', 'src/app.py', 'src/pkg/util.py']),
                         'admitted files are in every later snapshot')
        # What read refuses, search never opens; what search opens, read admits.
        (self.project / 'src' / 'secrets.py').write_text('API_KEY=abc\n')
        (self.project / 'src' / 'settings_prod.py').write_text('TOKEN=xyz\n')
        found = self.evidence.search_files(task_id, 'src', '=', **CTX)
        for hit in found['hits']:
            self.evidence.read_file(task_id, hit['path'], **CTX)
        self.assertIn('src/settings_prod.py', [h['path'] for h in found['hits']], 'a name filter is not a content guarantee: granted folders are readable')
        for denied in ('src/.env', 'src/credentials.json', 'src/link.py', 'src/hard.py', 'src/escape/other.py', 'sibling/other.py', 'docs/../greeting.py'):
            with self.subTest(denied=denied):
                with self.assertRaises(Rejected):
                    self.evidence.read_file(task_id, denied, **CTX)
                self.assertNotIn(denied, [h['path'] for h in found['hits']])
        with self.assertRaises(Rejected):
            self.evidence.read_file(self.task([]), 'src/app.py', **CTX)  # file-only task: nothing widened

    def test_granted_folder_may_not_contain_store_or_profile_roots(self):
        (self.project / 'vendor' / 'home').mkdir(parents=True)
        (self.project / 'vendor' / 'note.py').write_text('x\n')
        self.host.store.add_profile('q', 'codex', self.project / 'vendor' / 'home', 'q@example.test', 'personal')
        for op, args in (('task_create', {'text': 'x', 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['vendor']}),
                         ('coding_permission_grant', {'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['vendor']})):
            with self.subTest(op=op):
                with self.assertRaises(Rejected):
                    self.host.dispatch(op, {**self.scope, **args})
        self.assertEqual(self.evidence.read(self.task(['src'], files=('greeting.py', 'test_sample.py', 'vendor/note.py')), **CTX)['folders'], ['src'],
                         'an explicit sibling file grant beside the profile home is not banned')
        (self.host.root / 'test_sample.py').write_text(SAMPLE)
        self.host.store.add_project('anc', self.base, 'p', 'synthetic-model')
        self.host.store.add_thread('anc', 'anc', 'anc', 'private', ACTOR)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_create', {'projectId': 'anc', 'channelId': 'anc', 'text': 'x', 'files': ['test_sample.py'],
                                               'testFile': 'test_sample.py', 'folders': ['store']})

    def test_listing_and_search_stop_under_finite_bounds_and_report_partial_coverage(self):
        task_id = self.task()
        wide = self.project / 'src' / 'wide'
        wide.mkdir()
        for i in range(30):
            (wide / f'f{i:03d}.py').write_text('NEEDLE\n')
        with patch.object(TaskEvidence, 'ENTRY_LIMIT', 5):
            listing = self.evidence.list_files(task_id, 'src/wide', **CTX)
        self.assertLessEqual(len(listing['entries']), 5)
        self.assertTrue(listing['truncated'], 'enumeration stopped early is reported, never presented as complete')
        empty = self.project / 'src' / 'empty'
        for i in range(40):
            (empty / f'd{i:03d}').mkdir(parents=True)
        with patch.object(TaskEvidence, 'SEARCH_DIRECTORIES', 10):
            found = self.evidence.search_files(task_id, 'src/empty', 'NEEDLE', **CTX)
        self.assertLessEqual(found['scanned']['directories'], 10)
        self.assertTrue(found['truncated'])
        self.assertEqual(found['coverage'], 'partial')
        self.assertGreater(found['scanned']['unvisited_directories'], 0)
        (self.project / 'src' / 'big.py').write_bytes(b'NEEDLE' + b'x' * 262144)
        found = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertIn('src/big.py', [h['path'] for h in found['hits']], 'since beta.15 a file over 256 KiB is searched too')
        import task_evidence
        with patch.object(task_evidence, 'FILE_LIMIT', 262144):  # a file over the 16 MiB cap, in miniature
            found = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertEqual(found['scanned']['skipped_oversize'], 1)
        self.assertTrue(found['truncated'], 'a skipped file means the result is partial')
        self.assertNotIn('src/big.py', [h['path'] for h in found['hits']])
        # Security review N6 (24 Sep): one search stays bounded in time on the host loop, and at 16 MiB of reading.
        self.assertEqual((TaskEvidence.SEARCH_BYTES, TaskEvidence.SEARCH_SECONDS), (task_evidence.FILE_LIMIT, 3.0))
        found = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertFalse(found['scanned']['stopped_for_time'], 'control: a small search finishes in time')
        with patch.object(TaskEvidence, 'SEARCH_SECONDS', -1):
            found = self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        self.assertEqual(found['scanned']['files'], 1, 'out of time after the first file, which is always read')
        self.assertTrue(found['scanned']['stopped_for_time'])
        self.assertEqual((found['truncated'], found['coverage'], found['limits']['seconds']), (True, 'partial', -1))

    def test_absent_after_crash_creation_is_reconciled_only_when_provable_and_never_replayed(self):
        task_id = self.task()
        real_open = os.open

        def crash(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                raise KeyboardInterrupt()
            return real_open(path, flags, *args, **kwargs)
        with patch('task_evidence.os.open', crash):
            with self.assertRaises(KeyboardInterrupt):
                self.evidence.create_file(task_id, 'src/interrupted.py', b'x\n', **CTX)
        task = self.evidence.read(task_id, **CTX)
        self.assertEqual(task['created'], ['src/interrupted.py'], 'the persisted intent names a file that never appeared')
        self.assertFalse((self.project / 'src' / 'interrupted.py').exists())
        with self.assertRaises(Rejected):
            self.evidence.verify_current(task_id, **CTX)
        # A person creates content at that path before anyone reconciles: it is theirs and is never touched or claimed.
        (self.project / 'src' / 'interrupted.py').write_text('human wrote this\n')
        report = self.evidence.reconcile(task_id, **CTX)
        self.assertEqual(report['action'], 'inspect')
        self.assertEqual(self.evidence.read(task_id, **CTX)['attempts'][-1]['state'], 'uncertain')
        self.assertEqual((self.project / 'src' / 'interrupted.py').read_text(), 'human wrote this\n')
        with self.assertRaises(Rejected):
            self.evidence.create_file(task_id, 'src/interrupted.py', b'x\n', **CTX)
        (self.project / 'src' / 'interrupted.py').unlink()
        report = self.evidence.reconcile(task_id, **CTX)
        self.assertEqual(report['action'], 'dropped_absent_creation')
        task = self.evidence.read(task_id, **CTX)
        self.assertEqual(task['attempts'][-1]['state'], 'blocked')
        self.assertIn('absent', task['attempts'][-1]['blocker'])
        self.assertEqual(task['created'], [])
        self.assertFalse((self.project / 'src' / 'interrupted.py').exists(), 'reconciliation never replays the creation')
        self.assertEqual(self.evidence.reconcile(task_id, **CTX)['action'], 'none')
        self.assertEqual(self.evidence.verify_current(task_id, **CTX)['state'], 'prepared', 'the task continues on its recorded scope')

    def test_file_only_tasks_and_grants_are_unchanged(self):
        task_id = self.task([])
        task = self.evidence.read(task_id, **CTX)
        self.assertEqual((task['folders'], task['created']), ([], []))
        with self.assertRaises(Rejected):
            self.evidence.list_files(task_id, 'src', **CTX)
        with self.assertRaises(Rejected):
            self.evidence.search_files(task_id, 'src', 'NEEDLE', **CTX)
        with self.assertRaises(Rejected):
            self.evidence.read_file(task_id, 'src/app.py', **CTX)
        snap = self.evidence.read_file(task_id, 'greeting.py', **CTX)
        attempt = self.evidence.prepare(task_id, changes={'greeting.py': dict(expected_sha256=snap['sha256'], content=b'def greet(): return "hey"\n')}, **CTX)
        self.assertEqual(attempt['files'], ['greeting.py', 'test_sample.py'])
        from collaboration import prefix
        self.assertEqual(prefix(['a.py']), 'Development task. Use only dervo host tools. Approved existing files: ["a.py"]\n')
        self.assertIn('Granted folders', prefix(['a.py'], ['src']))


class ContractTests(unittest.TestCase):
    """Both provider schemas and the role rule, without any host or filesystem."""

    def test_folder_tools_are_offered_only_by_explicit_selection_and_identically_to_both_providers(self):
        from codex_coding import dynamic_tools, DEFAULT_TOOLS, FOLDER_TOOLS, validate_arguments
        from claude_policy import mcp_tools
        self.assertEqual([t['name'] for t in dynamic_tools()], list(DEFAULT_TOOLS), 'the default file-only contract is unchanged')
        offered = dynamic_tools(DEFAULT_TOOLS + FOLDER_TOOLS)
        self.assertEqual([t['name'] for t in offered], list(DEFAULT_TOOLS + FOLDER_TOOLS))
        for tool in offered:
            self.assertFalse(tool['inputSchema']['additionalProperties'])
            # Beta.15: dervo_read_file's line range is the one optional part of these tools.
            optional = {'start_line', 'end_line'} if tool['name'] == 'dervo_read_file' else set()
            self.assertEqual(sorted(tool['inputSchema']['required']), sorted(set(tool['inputSchema']['properties']) - optional))
        self.assertEqual([(t['name'], t['inputSchema']) for t in mcp_tools(DEFAULT_TOOLS + FOLDER_TOOLS)],
                         [(t['name'], t['inputSchema']) for t in offered], 'Claude receives the same schemas in MCP form')
        validate_arguments('dervo_list_files', {'directory': 'src/pkg'})
        validate_arguments('dervo_search_files', {'directory': 'src', 'query': 'NEEDLE'})
        validate_arguments('dervo_create_file', {'path': 'src/new.py', 'content': ''})
        # The project root is no longer a schema refusal. A task granted project access may list ".", so the
        # recorded grant decides, not the tool shape. BrokerTests.test_listing_is_bounded_nested_and_skips_links
        # _secrets_and_dot_entries proves a folder-only task is still refused the root, and test_project_access
        # .test_legacy_and_exclusions proves a task without the access grant is refused it too.
        validate_arguments('dervo_list_files', {'directory': '.'})
        for tool, args in (('dervo_list_files', {'directory': '/src'}),
                           ('dervo_list_files', {'directory': 'src/..'}), ('dervo_list_files', {'path': 'src'}),
                           ('dervo_search_files', {'directory': 'src', 'query': ''}),
                           ('dervo_search_files', {'directory': 'src', 'query': 'x' * 257}),
                           ('dervo_create_file', {'path': 'src/new.py', 'content': 'x', 'expected_hash': '0' * 64}),
                           ('dervo_create_file', {'path': '../new.py', 'content': 'x'})):
            with self.subTest(tool=tool, args=args):
                with self.assertRaises(Rejected):
                    validate_arguments(tool, args)

    def test_reviewers_and_file_only_tasks_never_get_folder_tools(self):
        from collaboration import Collaboration
        tools = Collaboration.tools_for
        self.assertEqual(tools(None, {'files': ['a.py']}), ('dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_run_tests'))
        self.assertEqual(tools(None, {'files': ['a.py'], 'folders': ['src']}),
                         ('dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_run_tests', 'dervo_list_files', 'dervo_search_files', 'dervo_create_file'))
        self.assertEqual(tools(None, {'role': 'reviewer', 'folders': ['src'], 'collaboration': {'agents': ['x']}}), ('dervo_read_file',))
        self.assertEqual(tools(None, {'role': 'reviewer', 'review_phase': 'initial', 'folders': ['src']}), ('dervo_read_file', 'dervo_review_verdict'))
        self.assertEqual(tools(None, {'role': 'worker', 'folders': [], 'collaboration': {'agents': [], 'depth': 1}}),
                         ('dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_run_tests', 'dervo_request_help'),
                         'a helper child is created with files only, so the grant never reaches it')


class ProviderDispatchTests(ProjectScopeBase):
    """The whole production path: project permission with folders, a conversation request, the provider adapter's tool
    contract, the host broker, evidence and human review. The provider is a synthetic peer that lists, searches, creates,
    then runs the tests."""

    def peer_patch(self, script):
        test = self
        if self.provider == 'codex':
            from test_codex_coding import SyntheticCodingPeer, SID

            class Peer(SyntheticCodingPeer):
                def __init__(peer, *a, **kw):
                    super().__init__(*a, **kw)
                    test.peers.append(peer)

                def tool(peer, name, args, number):
                    peer.queue.append({'id': str(number), 'method': 'item/tool/call', 'params': dict(
                        threadId=SID, turnId='turn-1', callId=str(number), tool=name, arguments=args)})

                def finish(peer, text='Created the module and ran the tests.'):
                    peer.queue.extend([
                        {'method': 'item/agentMessage/delta', 'params': dict(threadId=SID, turnId='turn-1', itemId='answer', delta=text)},
                        {'method': 'turn/completed', 'params': {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'completed'}}}])

                def send(peer, message):
                    if message.get('method') == 'thread/start':
                        test.offered = [t['name'] for t in message['params']['dynamicTools']]
                    if message.get('method') == 'turn/start':
                        test.context_text = message['params']['input'][0]['text']
                        peer.queue.append({'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}})
                        script(peer, None, None)
                    elif type(message.get('id')) is str and message['id'].isdigit():
                        script(peer, int(message['id']), json.loads(message['result']['contentItems'][0]['text']))
                    else:
                        super().send(message)
            return patch('codex_coding.OwnedProcess', Peer)
        from test_claude_coding import SyntheticClaudePeer, verified_auth, CLAUDE
        import claude_coding

        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)
                test.offered = argv[argv.index('--allowedTools') + 1].split(',')

            def send(peer, message):
                if message.get('type') == 'user':
                    test.context_text = message['message']['content']
                if message.get('type') == 'control_response':
                    mcp = message['response'].get('response', {}).get('mcp_response')
                    if mcp and mcp.get('id') == 'list':
                        test.listed = [t['name'] for t in mcp['result']['tools']]
                super().send(message)

            def on_prompt(peer):
                script(peer, None, None)

            def on_tool_result(peer, mid, result):
                script(peer, mid, json.loads(result['content'][0]['text']))
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(claude_coding, 'auth_status', verified_auth))
        stack.enter_context(patch.object(claude_coding, 'executable', lambda: CLAUDE))
        stack.enter_context(patch.object(claude_coding, 'OwnedProcess', Peer))
        return stack

    def dervo_tools(self):
        """The Dervo tools this run offered, provider prefix removed.

        Asserting the exact offered list aged badly. Bookkeeping tools that never touch the filesystem keep
        being added (the task guide, memory, history, chat), and each one turned these red without anything
        about the scope contract changing. What the scope contract says is which of the file and folder tools
        are present, and that nothing outside the adapter's own catalogue is offered under the Dervo name.
        Tools the provider brings itself, like Claude's WebSearch, are not part of this contract.
        """
        from codex_coding import ALL_TOOLS
        names = [name.removeprefix('mcp__dervo__') for name in self.offered if 'dervo_' in name]
        self.assertEqual(len(set(names)), len(names), 'no tool is offered twice')
        self.assertEqual(set(names) - set(ALL_TOOLS), set(), 'only tools the adapter defines are offered')
        return names

    def settle(self, task_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.host.tick()
            detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})
            if detail['task']['state'] != 'working':
                return detail
            time.sleep(.01)
        self.fail('Synthetic completion timed out')

    def grant(self, folders=('src',)):
        granted = self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py',
                                                                 **({'folders': list(folders)} if folders else {})})
        # Result approval is a separate policy, off by default for a new project; this suite exercises the explicit human review.
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})
        return granted

    def run_script(self, script, text='Add src/pkg/new_module.py'):
        self.peers, self.replies = [], {}
        with self.peer_patch(script):
            started = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': text})
            self.assertIsNotNone(started['id'], started)
            return started, self.settle(started['taskId'])

    def test_permission_with_folders_is_explicit_shown_once_and_revocable(self):
        for folders in (['.'], ['missing'], ['src/escape'], ['sibling', 'sibling'], ['src/link.py']):
            with self.subTest(folders=folders):
                with self.assertRaises(Rejected):
                    self.grant(folders)
        plain = self.grant(())
        self.assertEqual((plain['state'], plain['folders']), ('granted', []))
        self.assertIn('No folder is granted', plain['creation_detail'])
        self.assertNotIn('folder', plain['detail'])
        granted = self.grant(['src', 'docs'])
        self.assertEqual((granted['state'], granted['folders']), ('granted', ['docs', 'src']))
        self.assertIn('read, edit and create regular files inside 2 granted folder(s)', granted['detail'])
        self.assertIn('docs, src', granted['creation_detail'])
        self.assertIn('do not prove', granted['folder_risk'], 'the grant copy keeps the truthful limit on name filtering')
        self.assertIn('stay denied', granted['creation_detail'])
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['coding']['folders'], ['docs', 'src'])
        (self.project / 'docs').rmdir()
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'stale')
        revoked = self.host.dispatch('coding_permission_revoke', self.scope)
        self.assertEqual((revoked['state'], revoked['folders']), ('missing', []))

    def test_provider_lists_searches_creates_and_verifies_through_the_host(self):
        self.grant()

        def script(peer, mid, reply):
            if mid is not None:
                self.replies[mid] = reply
            if mid is None:
                peer.tool('dervo_list_files', {'directory': 'src'}, 1)
            elif mid == 1:
                peer.tool('dervo_search_files', {'directory': 'src', 'query': 'NEEDLE'}, 2)
            elif mid == 2:
                peer.tool('dervo_list_files', {'directory': 'sibling'}, 3)
            elif mid == 3:
                peer.tool('dervo_create_file', {'path': 'src/pkg/new_module.py', 'content': 'VALUE = 2\n'}, 4)
            elif mid == 4:
                peer.tool('dervo_create_file', {'path': 'src/pkg/new_module.py', 'content': 'again\n'}, 5)
            elif mid == 5:
                peer.tool('dervo_run_tests', {}, 6)
            elif mid == 6:
                peer.finish()
        started, detail = self.run_script(script)
        task, evidence = detail['task'], detail['evidence']
        self.assertEqual(task['folders'], ['src'])
        from codex_coding import DEFAULT_TOOLS, FOLDER_TOOLS
        offered = self.dervo_tools()
        self.assertEqual([t for t in offered if t in DEFAULT_TOOLS + FOLDER_TOOLS], list(DEFAULT_TOOLS + FOLDER_TOOLS),
                         'a folder grant offers every file and folder tool, in the contract order')
        if self.provider == 'claude':
            self.assertIn('dervo_create_file', self.listed, 'tools/list offers the same contract')
        self.assertIn('Granted folders', self.context_text)
        self.assertIn('"src"', self.context_text)
        self.assertEqual([e['name'] for e in self.replies[1]['entries']], ['app.py', 'pkg'])
        self.assertEqual([h['path'] for h in self.replies[2]['hits']], ['src/pkg/util.py', 'src/pkg/deep/leaf.py'])
        self.assertIn('outside the granted folders', self.replies[3]['error'], 'an ungranted sibling is refused truthfully')
        self.assertEqual((self.replies[4]['created'], self.replies[4]['attempt'], self.replies[4]['state']), ('src/pkg/new_module.py', 1, 'prepared'))
        self.assertIn('already exists', self.replies[5]['error'], 'a second creation of the same target conflicts')
        self.assertEqual((self.project / 'src/pkg/new_module.py').read_text(), 'VALUE = 2\n', 'the conflict never overwrote it')
        states = [(t['tool'], t['state'], t['success']) for t in task['tokens']]
        self.assertEqual(states, [('dervo_list_files', 'delivered', True), ('dervo_search_files', 'delivered', True), ('dervo_list_files', 'delivered', False),
                                  ('dervo_create_file', 'delivered', True), ('dervo_create_file', 'delivered', False), ('dervo_run_tests', 'delivered', True)])
        self.assertEqual([a['kind'] for a in evidence['attempts']], ['create'])
        attempt = evidence['attempts'][0]
        self.assertIsNone(attempt['before']['src/pkg/new_module.py'])
        self.assertIn('+VALUE = 2', attempt['diff']['src/pkg/new_module.py'])
        self.assertEqual(attempt['state'], 'verified', attempt.get('blocker'))
        self.assertEqual(attempt['test']['exit_status'], 0, attempt['test'])
        self.assertEqual(task['state'], 'ready_for_review')
        audit = {a['token']: a for a in detail['broker_audit']}
        for token in task['tokens']:
            self.assertIsNotNone(audit[token['token']]['response'], 'every tool call, refused or not, is inspectable')
        reviewed = self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': 1, 'decision': 'approved'})
        self.assertEqual(reviewed['state'], 'idle')

    def test_file_only_grant_offers_no_folder_tools_and_refuses_them(self):
        self.grant(())

        def script(peer, mid, reply):
            if mid is not None:
                self.replies[mid] = reply
            if mid is None:
                peer.tool('dervo_read_file', {'path': 'greeting.py'}, 1)
            elif mid == 1:
                peer.tool('dervo_create_file', {'path': 'src/new.py', 'content': 'x\n'}, 2)
            elif mid == 2:
                peer.finish('Could not create the file.')
        started, detail = self.run_script(script, 'Create src/new.py')
        from codex_coding import DEFAULT_TOOLS, FOLDER_TOOLS
        offered = self.dervo_tools()
        self.assertEqual([t for t in offered if t in DEFAULT_TOOLS], list(DEFAULT_TOOLS), 'the file tools are all offered')
        self.assertEqual([t for t in offered if t in FOLDER_TOOLS], [], 'a file-only grant offers no folder tool at all')
        self.assertNotIn('Granted folders', self.context_text)
        self.assertIn('sha256', self.replies[1])
        self.assertNotIn(2, self.replies, 'the adapter refuses a tool outside the contract before the broker; no result is fabricated')
        self.assertFalse((self.project / 'src' / 'new.py').exists())
        # The provider sent a call outside its contract mid-turn: the adapter blocks, the run is recorded as uncertain, nothing replays.
        self.assertEqual(detail['task']['state'], 'disconnected', detail['task']['error'])
        self.assertEqual(detail['task']['runs'][-1]['state'], 'uncertain')
        self.assertEqual(detail['evidence']['attempts'], [])
        self.assertFalse(detail['retry']['available'])

    def test_stop_during_creation_verification_keeps_the_created_file_uncertain_and_never_replays(self):
        self.grant()

        def script(peer, mid, reply):
            if mid is None:
                peer.tool('dervo_create_file', {'path': 'src/pkg/new_module.py', 'content': 'VALUE = 2\n'}, 1)
            elif mid == 1:
                peer.tool('dervo_run_tests', {}, 2)
        self.peers = []
        with self.peer_patch(script):
            started = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'create it'})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': started['taskId']})
                if any(t['tool'] == 'dervo_run_tests' and t['state'] in ('running', 'queued') for t in detail['task']['tokens']):
                    break
                time.sleep(.01)
            else:
                self.fail('verification never started')
            self.host.dispatch('task_stop', {**self.scope, 'taskId': started['taskId']})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': started['taskId']})
                if detail['task']['state'] != 'working' and detail['evidence']['attempts'][-1]['state'] != 'running':
                    break
                time.sleep(.01)
        task, attempts = detail['task'], detail['evidence']['attempts']
        self.assertEqual([a['kind'] for a in attempts], ['create'])
        self.assertIn(attempts[-1]['state'], ('cancelled', 'verified', 'blocked'))
        self.assertEqual((self.project / 'src/pkg/new_module.py').read_text(), 'VALUE = 2\n', 'the created file stays; nothing is rolled back or replayed')
        self.assertNotEqual(task['state'], 'working')
        self.assertFalse(detail['retry']['available'], 'a run with a recorded creation is never silently retried')
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'different text'})
        again = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'create it'})
        self.assertEqual(again['taskId'], started['taskId'], 'the same request id returns the same task, no second creation')


    def crash_creation(self, task_id, path='src/pkg/interrupted.py'):
        """An interrupted creation through the host's own broker: intent persisted, nothing on disk, attempt uncertain."""
        real_open = os.open

        def crash(p, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                raise KeyboardInterrupt()
            return real_open(p, flags, *args, **kwargs)
        with patch('task_evidence.os.open', crash):
            with self.assertRaises(KeyboardInterrupt):
                self.host.coding.evidence.create_file(task_id, path, b'x\n', **CTX)

    def test_reconcile_is_a_host_operation_that_settles_only_an_absent_creation_and_replays_nothing(self):
        self.grant()
        task = self.host.dispatch('task_create', {**self.scope, 'text': 'create it', 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['src']})
        self.host.coding.evidence.approve(task['id'], scope_version=1, **CTX)
        scope = {**self.scope, 'taskId': task['id']}
        settled = self.host.dispatch('task_detail', scope)['reconcile']
        self.assertEqual(settled['available'], False)
        self.assertIn('No interrupted', settled['reason'])
        self.crash_creation(task['id'])
        before = self.host.dispatch('task_detail', scope)
        self.assertEqual(before['reconcile'], {'available': True, 'attempt': 1, 'path': 'src/pkg/interrupted.py',
                                               'reason': 'Creation of src/pkg/interrupted.py was interrupted before confirmation.'})
        self.assertEqual(before['evidence']['attempts'][-1]['state'], 'uncertain')
        # Present target: a person wrote there since. Reported, never touched, still uncertain.
        (self.project / 'src/pkg/interrupted.py').write_text('human wrote this\n')
        report = self.host.dispatch('task_reconcile', scope)
        self.assertEqual((report['action'], report['path']), ('inspect', 'src/pkg/interrupted.py'))
        self.assertIn('kept untouched', report['detail'])
        self.assertEqual((self.project / 'src/pkg/interrupted.py').read_text(), 'human wrote this\n')
        after = self.host.dispatch('task_detail', scope)
        self.assertEqual(after['evidence']['attempts'][-1]['state'], 'uncertain')
        self.assertEqual(after['reconcile']['available'], True, 'a present target keeps the action; moving the file aside and reconciling again is the next step')
        # Absent target: the phantom intent is settled; the file is not created and nothing else changes.
        (self.project / 'src/pkg/interrupted.py').unlink()
        report = self.host.dispatch('task_reconcile', scope)
        self.assertEqual(report['action'], 'dropped_absent_creation')
        after = self.host.dispatch('task_detail', scope)
        self.assertEqual(after['evidence']['attempts'][-1]['state'], 'blocked')
        self.assertEqual(len(after['evidence']['attempts']), len(before['evidence']['attempts']), 'reconcile adds no attempt')
        self.assertEqual((after['task']['tokens'], after['task']['messages'], after['task']['runs']),
                         (before['task']['tokens'], before['task']['messages'], before['task']['runs']), 'history is preserved verbatim')
        self.assertFalse((self.project / 'src/pkg/interrupted.py').exists(), 'nothing is replayed')
        self.assertEqual(after['reconcile']['available'], False)
        self.assertEqual(self.host.dispatch('task_reconcile', scope)['action'], 'none')
        with self.assertRaises(Rejected):
            self.host.dispatch('task_reconcile', {**scope, 'attempt': 1})

    def test_reconcile_is_refused_while_the_provider_or_a_check_is_still_active(self):
        self.grant()

        def script(peer, mid, reply):
            if mid is None:
                peer.tool('dervo_list_files', {'directory': 'src'}, 1)
            # then the provider hangs: the turn stays open with nothing settled
        self.peers = []
        with self.peer_patch(script):
            started = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'list it'})
            scope = {**self.scope, 'taskId': started['taskId']}
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', scope)
                if detail['task']['tokens'] and detail['task']['tokens'][0]['state'] == 'delivered':
                    break
                time.sleep(.01)
            else:
                self.fail('listing never delivered')
            self.assertEqual(detail['task']['state'], 'working')
            self.assertEqual(detail['reconcile']['available'], False)
            self.assertIn('Nothing to reconcile', detail['reconcile']['reason'], 'no creation was interrupted; the action stays off and says so')
            with self.assertRaises(Rejected) as refused:
                self.host.dispatch('task_reconcile', scope)
            self.assertIn('still active', str(refused.exception))
            self.host.dispatch('task_stop', scope)
            detail = self.settle(started['taskId'])
        self.assertNotEqual(detail['task']['state'], 'completed')
        self.assertEqual(detail['reconcile']['available'], False, 'no uncertain creation: nothing to reconcile, and that is said')

    def test_revoking_or_narrowing_the_grant_mid_run_refuses_every_later_folder_operation(self):
        self.grant()
        (self.project / 'docs' / 'guide.py').write_text('# docs\n')
        self.grant(['src', 'docs'])

        def script(peer, mid, reply):
            if mid is not None:
                self.replies[mid] = reply
            if mid is None:
                peer.tool('dervo_list_files', {'directory': 'src'}, 1)
            elif mid == 1:
                peer.tool('dervo_read_file', {'path': 'src/app.py'}, 2)  # admitted while the grant still holds
            elif mid == 7:
                peer.tool('dervo_run_tests', {}, 8)
            elif mid == 8:
                peer.finish('Did what was allowed.')
        self.peers, self.replies = [], {}
        with self.peer_patch(script):
            started = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'work in src'})
            scope = {**self.scope, 'taskId': started['taskId']}
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.host.tick()
                if 2 in self.replies:
                    break
                time.sleep(.01)
            else:
                self.fail('the read never returned')
            self.assertEqual([e['name'] for e in self.replies[1]['entries']], ['app.py', 'pkg'])
            self.assertIn('sha256', self.replies[2])
            # The grant is narrowed to docs only while the provider turn is still open.
            narrowed = self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['docs']})
            self.assertEqual(narrowed['folders'], ['docs'])
            peer = self.peers[-1]
            peer.tool('dervo_list_files', {'directory': 'src'}, 3)
            peer.tool('dervo_search_files', {'directory': 'src', 'query': 'NEEDLE'}, 4)
            peer.tool('dervo_create_file', {'path': 'src/pkg/late.py', 'content': 'late\n'}, 5)
            peer.tool('dervo_read_file', {'path': 'src/pkg/util.py'}, 6)  # never admitted before the grant changed
            peer.tool('dervo_write_file', {'path': 'src/app.py', 'expected_hash': self.replies[2]['sha256'], 'content': 'def main():\n    return 1\n'}, 7)
            detail = self.settle(started['taskId'])
        for mid in (3, 4, 5, 6):
            with self.subTest(mid=mid):
                self.assertIn('no longer grants src', self.replies[mid]['error'])
        self.assertIn('sha256', self.replies[2])
        self.assertEqual(self.replies[7].get('state'), 'prepared', 'a file admitted before the change stays in the recorded scope')
        # The check is refused for the same reason, because the scoped files sit under the withdrawn folder. The
        # run therefore ends without verification evidence and says so, rather than checking a scope it may no
        # longer read. Narrowing a grant mid-run costs you the run; it never buys the provider a wider one.
        self.assertIn('no longer grants src', self.replies[8]['error'])
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('verification evidence is missing', detail['task']['error'])
        self.assertFalse((self.project / 'src/pkg/late.py').exists(), 'no artifact appears under the withdrawn folder')
        self.assertEqual(detail['evidence']['admitted'], ['src/app.py'], 'nothing new was admitted after the change')
        self.assertNotEqual(detail['task']['state'], 'completed')
        self.assertEqual(detail['task']['folders'], ['docs', 'src'], "the task's recorded scope is never rewritten")
        # Revoked entirely: a continuation starts nothing.
        self.host.dispatch('coding_permission_revoke', self.scope)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue', {**scope, 'text': 'go on'})
        self.assertEqual(sorted(p.name for p in (self.project / 'src' / 'pkg').iterdir()), ['deep', 'util.py'])

    def test_a_changed_or_revoked_permission_refuses_to_continue_a_finished_task(self):
        """The grant is rechecked when a human asks for a correction, not only when the task is created.

        Split out of the mid-run test, which can never reach this: narrowing the grant mid-turn also blocks
        the check, so that task has no completed evidence to continue from.
        """
        self.grant()

        def script(peer, mid, reply):
            if mid is None:
                peer.tool('dervo_create_file', {'path': 'src/pkg/new_module.py', 'content': 'VALUE = 2\n'}, 1)
            elif mid == 1:
                peer.tool('dervo_run_tests', {}, 2)
            elif mid == 2:
                peer.finish()
        started, detail = self.run_script(script)
        scope = {**self.scope, 'taskId': started['taskId']}
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual(attempt['state'], 'verified', attempt.get('blocker'))
        self.host.dispatch('task_review', {**scope, 'attemptNumber': attempt['number'], 'decision': 'rejected'})
        for change in ({'folders': ['docs']}, None):
            with self.subTest(change=change):
                if change is None:
                    self.host.dispatch('coding_permission_revoke', self.scope)
                else:
                    (self.project / 'docs' / 'guide.py').write_text('# docs\n')
                    self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'],
                                                                   'testFile': 'test_sample.py', **change})
                with self.assertRaises(Rejected) as refused:
                    self.host.dispatch('task_continue', {**scope, 'text': 'go on'})
                self.assertIn('no longer current', str(refused.exception))


class ClaudeProviderDispatchTests(ProviderDispatchTests):
    provider = 'claude'


if __name__ == '__main__':
    unittest.main()
