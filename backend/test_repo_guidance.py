"""The repository's instruction files through Knowledge (PLAN-native.md A6), and Knowledge's undo operations.

Real Host and store, a temporary project folder, fake provider processes. Nothing here launches a real provider, reads
credentials or opens anything on the Mac: ``open`` is patched.
"""
import json
import platform_os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import repo_guidance
from dervo_connections import Rejected
from host import Host
from test_conversation_coding import ConversationCodingTests

MARKER = 'REPO_CLAUDE_MD_MARKER say the word teal'


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class RepoFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project'
        (self.root / '.claude' / 'rules').mkdir(parents=True)
        self.host = Host(Path(self.temp.name) / 'store')
        self.addCleanup(self.host.close)
        self.binding = dict(project_id='p', cwd=str(self.root))

    def test_files_are_found_in_delivery_order_and_nothing_else_is(self):
        (self.root / 'AGENTS.md').write_text('agents\n')
        (self.root / 'CLAUDE.md').write_text('claude\n')
        (self.root / '.claude' / 'CLAUDE.md').write_text('inner\n')
        (self.root / '.claude' / 'rules' / 'testing.md').write_text('tests\n')
        (self.root / '.claude' / 'rules' / 'b-release.md').write_text('release\n')
        (self.root / '.claude' / 'rules' / 'notes.txt').write_text('not a rule\n')
        (self.root / '.claude' / 'settings.json').write_text('{"hooks": {}}')
        (self.root / 'README.md').write_text('readme\n')
        self.assertEqual(repo_guidance.candidates(self.root),
                         ['CLAUDE.md', '.claude/CLAUDE.md', '.claude/rules/b-release.md', '.claude/rules/testing.md', 'AGENTS.md'])

    def test_a_file_over_32_kib_is_cut_at_a_character_and_says_so(self):
        # 'é' is two bytes, so byte 32768 falls inside one: the cut backs off to a whole character.
        (self.root / 'CLAUDE.md').write_bytes(b'a' + 'é'.encode() * 20000)
        entry = repo_guidance.read_file(self.root, 'CLAUDE.md')
        self.assertTrue(entry['cut'])
        self.assertEqual(entry['bytes'], 40001)
        self.assertLessEqual(entry['sent_bytes'], repo_guidance.LIMIT)
        self.assertGreater(entry['sent_bytes'], repo_guidance.LIMIT - 4)
        self.assertTrue(entry['text'].endswith('é'))
        record = repo_guidance.snapshot(self.host.store.db, self.binding)
        self.assertEqual(record['files'][0]['cut'], True)
        self.assertIn('--- CLAUDE.md (the first 31 KB of 39 KB, the rest was cut) ---', record['text'])
        # Negative control: a file at the limit exactly is whole.
        (self.root / 'CLAUDE.md').write_bytes(b'b' * repo_guidance.LIMIT)
        whole = repo_guidance.read_file(self.root, 'CLAUDE.md')
        self.assertFalse(whole['cut'])
        self.assertEqual(whole['sent_bytes'], repo_guidance.LIMIT)

    def test_unreadable_and_outside_files_are_listed_with_a_reason_and_never_sent(self):
        (self.root / '.claude' / 'rules' / 'release.md').write_bytes(b'\xff\xfe binary')
        outside = Path(self.temp.name) / 'outside.md'
        outside.write_text('SECRET_OUTSIDE\n')
        (self.root / 'AGENTS.md').symlink_to(outside)
        listed = {f['path']: f for f in repo_guidance.listing(self.host.store.db, self.binding)['files']}
        self.assertEqual(listed['.claude/rules/release.md']['error'], 'Couldn’t read it. It isn’t UTF-8 text, so tasks don’t get it.')
        self.assertEqual(listed['AGENTS.md']['error'], 'It points outside the project, so tasks don’t get it.')
        record = repo_guidance.snapshot(self.host.store.db, self.binding)
        self.assertEqual(record['text'], '')
        self.assertEqual({item['path']: item['reason'] for item in record['left_out']},
                         {'.claude/rules/release.md': 'unreadable', 'AGENTS.md': 'unreadable'})
        self.assertNotIn('SECRET_OUTSIDE', json.dumps(record))

    def test_stop_sending_and_changed_since_the_last_task(self):
        (self.root / 'AGENTS.md').write_text('version one\n')
        db = self.host.store.db
        first = repo_guidance.snapshot(db, self.binding)
        self.assertIn('version one', first['text'])
        listed = repo_guidance.listing(db, self.binding)['files'][0]
        self.assertFalse(listed['changed'], 'unchanged since the last task')
        (self.root / 'AGENTS.md').write_text('version two\n')
        self.assertTrue(repo_guidance.listing(db, self.binding)['files'][0]['changed'])
        repo_guidance.set_sending(db, self.binding, 'AGENTS.md', False)
        stopped = repo_guidance.snapshot(db, self.binding)
        self.assertEqual(stopped['text'], '')
        self.assertEqual(stopped['left_out'], [dict(path='AGENTS.md', reason='stopped')])
        self.assertTrue(repo_guidance.listing(db, self.binding)['files'][0]['stopped'])
        self.assertEqual((self.root / 'AGENTS.md').read_text(), 'version two\n', 'the file is never changed')
        repo_guidance.set_sending(db, self.binding, 'AGENTS.md', True)
        again = repo_guidance.snapshot(db, self.binding)
        self.assertIn('version two', again['text'])
        self.assertFalse(repo_guidance.listing(db, self.binding)['files'][0]['changed'], 'the last task took the new version')
        for path in ('README.md', '../AGENTS.md', '.claude/rules/../../x.md', '/etc/hosts', 3):
            with self.subTest(path=path), self.assertRaises(Rejected):
                repo_guidance.set_sending(db, self.binding, path, False)

    def test_open_and_reveal_only_the_listed_files(self):
        (self.root / 'CLAUDE.md').write_text('x\n')
        calls = []
        repo_guidance.reveal(self.binding, 'CLAUDE.md', 'finder', run=calls.append)
        repo_guidance.reveal(self.binding, 'CLAUDE.md', 'editor', run=calls.append)
        self.assertEqual(calls, [platform_os.reveal_command(str(self.root / 'CLAUDE.md')), platform_os.open_as_text_command(str(self.root / 'CLAUDE.md'))])
        for path, how in (('AGENTS.md', 'editor'), ('README.md', 'finder'), ('CLAUDE.md', 'run')):
            with self.subTest(path=path, how=how), self.assertRaises(Rejected):
                repo_guidance.reveal(self.binding, path, how, run=calls.append)
        self.assertEqual(len(calls), 2)


    def test_a_linked_instruction_file_is_never_opened(self):
        # Security review, 23 Sep 2026, H2: a cloned repo points CLAUDE.md at a script, and a plain `open` would run it.
        script = self.root / 'setup.command'; script.write_text('#!/bin/sh\necho ran\n'); script.chmod(0o755)
        (self.root / 'CLAUDE.md').symlink_to(script)
        (self.root / 'real').mkdir(); (self.root / 'real' / 'style.md').write_text('rules\n')
        rules = self.root / '.claude' / 'rules'
        if rules.exists():
            shutil.rmtree(rules)  # setUp's real rules folder; this test needs it to be a link
        rules.parent.mkdir(exist_ok=True); rules.symlink_to(self.root / 'real')
        calls = []
        for path in ('CLAUDE.md', '.claude/rules/style.md'):
            for how in ('editor', 'finder'):
                with self.subTest(path=path, how=how), self.assertRaisesRegex(Rejected, 'link to another file'):
                    repo_guidance.reveal(self.binding, path, how, run=calls.append)
        self.assertEqual(calls, [], 'nothing is opened or revealed')
        (self.root / 'AGENTS.md').write_text('plain\n')
        repo_guidance.reveal(self.binding, 'AGENTS.md', 'editor', run=calls.append)
        self.assertEqual(calls, [platform_os.open_as_text_command(str(self.root / 'AGENTS.md'))], 'a real file still opens, as text')

class KnowledgeUndo(unittest.TestCase):
    """Undo in Knowledge: an approved or dismissed lesson waits again, a waiting rule version can be set aside and brought
    back, archive records the version it took out of use, and a removed note comes back."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.host = Host(base / 'store')
        self.addCleanup(self.host.close)
        (base / 'project').mkdir()
        (base / 'project' / 'AGENTS.md').write_text('# Agents\n')
        self.host.store.add_profile('p', 'claude', base / 'store/profiles/p', 'personal@example.test', 'personal')
        self.host.store.add_project('p', base / 'project', 'p', 'synthetic-model')
        self.scope = dict(projectId='p', channelId='', profileId='p')

    def call(self, op, **args):
        return self.host.dispatch(op, {**self.scope, **args})

    def lesson(self, status='pending'):
        import uuid
        from project_knowledge import ProjectKnowledge
        ProjectKnowledge(self.host.store)
        lesson_id = str(uuid.uuid4())
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO project_lessons VALUES (?,?,?,?,?,?,?)', (lesson_id, 'p', 'p', 't', 'r', status,
                json.dumps(dict(id=lesson_id, text='Check the plan files are committed.', evidence='They weren’t.', provider='claude', source=None))))
        return lesson_id

    def test_listing_carries_the_repository(self):
        repository = self.call('knowledge_list')['repository']
        self.assertEqual([f['path'] for f in repository['files']], ['AGENTS.md'])
        self.assertEqual(repository['missing'], ['CLAUDE.md', '.claude/CLAUDE.md', '.claude/rules'])
        self.assertEqual(self.call('knowledge_repo_list'), repository)

    def test_the_repository_loads_on_a_work_account_where_rules_are_refused(self):
        with self.host.store.db:
            self.host.store.db.execute("UPDATE profiles SET category='work' WHERE id='p'")
        with self.assertRaises(Rejected):
            self.call('knowledge_list')
        self.assertEqual([f['path'] for f in self.call('knowledge_repo_list')['files']], ['AGENTS.md'])
        # Negative control: another account can't read this project's files.
        with self.assertRaises(Rejected):
            self.host.dispatch('knowledge_repo_list', {**self.scope, 'profileId': 'someone-else'})

    def test_undo_of_an_approved_lesson_takes_its_rule_out_of_use_and_it_waits_again(self):
        lesson_id = self.lesson()
        with patch('project_knowledge.ProjectKnowledge.source'):
            rule = self.call('knowledge_lesson_keep', lessonId=lesson_id)
            rule = self.call('knowledge_approve', ruleId=rule['id'], versionId=rule['versions'][0]['id'], expectedRevision=rule['revision'])
            self.assertEqual(self.call('knowledge_lesson_reopen', lessonId=lesson_id), {'reopened': True})
        saved = json.loads(self.host.store.db.execute('SELECT document FROM project_knowledge WHERE id=?', (rule['id'],)).fetchone()[0])
        self.assertIsNone(saved['active_version_id'], 'nothing it said reaches the next task')
        self.assertEqual(saved['events'][-1]['kind'], 'archived')
        self.assertEqual(saved['events'][-1]['archived_version_id'], rule['versions'][0]['id'])
        with self.host.store.db:
            status = self.host.store.db.execute('SELECT status FROM project_lessons WHERE id=?', (lesson_id,)).fetchone()[0]
        self.assertEqual(status, 'pending')

    def test_undo_of_a_dismissed_lesson(self):
        lesson_id = self.lesson()
        with patch('project_knowledge.ProjectKnowledge.source'):
            self.call('knowledge_lesson_dismiss', lessonId=lesson_id)
            self.call('knowledge_lesson_reopen', lessonId=lesson_id)
            # Negative control: a lesson already waiting has nothing to undo.
            self.assertEqual(self.call('knowledge_lesson_reopen', lessonId=lesson_id), {'reopened': False})
        with self.host.store.db:
            self.assertEqual(self.host.store.db.execute('SELECT status FROM project_lessons WHERE id=?', (lesson_id,)).fetchone()[0], 'pending')

    def test_a_waiting_version_is_dismissed_and_brought_back_without_touching_the_version_in_use(self):
        rule = self.call('knowledge_save', ruleId=None, expectedRevision=0, text='No em dashes.', provenance='Added in Dervo.', source=None)
        rule = self.call('knowledge_approve', ruleId=rule['id'], versionId=rule['versions'][0]['id'], expectedRevision=1)
        rule = self.call('knowledge_save', ruleId=rule['id'], expectedRevision=2, text='No em dashes anywhere.', provenance='Added in Dervo.', source=None)
        newer = rule['versions'][-1]['id']
        rule = self.call('knowledge_version_dismiss', ruleId=rule['id'], versionId=newer, expectedRevision=3, dismissed=True)
        self.assertEqual((rule['events'][-1]['kind'], rule['events'][-1]['version_id']), ('dismissed', newer))
        self.assertEqual(rule['active_version_id'], rule['versions'][0]['id'])
        rule = self.call('knowledge_version_dismiss', ruleId=rule['id'], versionId=newer, expectedRevision=4, dismissed=False)
        self.assertEqual(rule['events'][-1]['kind'], 'reopened')
        with self.assertRaises(Rejected):
            self.call('knowledge_version_dismiss', ruleId=rule['id'], versionId=rule['versions'][0]['id'], expectedRevision=5, dismissed=True)
        with self.assertRaises(Rejected):
            self.call('knowledge_version_dismiss', ruleId=rule['id'], versionId=newer, expectedRevision=1, dismissed=True)

    def test_a_removed_note_comes_back_while_there_is_room(self):
        import project_memory
        db = self.host.store.db
        project_memory.schema(db)
        with db:
            for index in range(project_memory.MAX_ENTRIES + 1):
                db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', (f'n{index}', 'p', 'personal', 'active' if index else 'removed',
                           json.dumps(dict(id=f'n{index}', text=f'note {index}', created_at=1, updated_at=1))))
        with self.assertRaises(Rejected) as full:
            self.call('knowledge_note_restore', entryId='n0')
        self.assertIn('full', str(full.exception))
        with db:
            db.execute("UPDATE project_memory SET status='removed' WHERE id='n1'")
        view = self.call('knowledge_note_restore', entryId='n0')
        self.assertIn('n0', [e['id'] for e in view['entries']])
        with self.assertRaises(Rejected):
            self.call('knowledge_note_restore', entryId='n0')

    def test_a_skill_proposal_goes_back_to_waiting_and_the_earlier_version_is_in_use_again(self):
        from project_skills import ProjectSkills
        skills = ProjectSkills(self.host.store)
        scope = dict(projectId='p', profileId='p')
        listing = skills.dispatch('skills_save', {**scope, 'skillId': None, 'expectedRevision': 0, 'name': 'cut-a-beta',
                                                  'description': 'Cutting a beta.', 'body': '1. Build.'})
        skill = listing['skills'][0]
        record = skills.find('p', 'p', skill['id'])
        with self.host.store.db:
            skills.add_version(record, name='cut-a-beta', description='Cutting a beta.', body='1. Build.\n2. Sign.', status='proposed', origin='agent', author='claude')
            record['revision'] += 1
            skills.write('p', 'p', record)
        listing = skills.dispatch('skills_review', {**scope, 'skillId': skill['id'], 'expectedRevision': 2, 'version': 2, 'decision': 'approve'})
        self.assertEqual(listing['skills'][0]['current']['version'], 2)
        listing = skills.dispatch('skills_review', {**scope, 'skillId': skill['id'], 'expectedRevision': 3, 'version': 2, 'decision': 'reopen'})
        self.assertEqual(listing['skills'][0]['current']['version'], 1)
        self.assertEqual([p['version'] for p in listing['skills'][0]['proposals']], [2])
        # Negative control: your own saved version never goes to waiting.
        with self.assertRaises(Rejected):
            skills.dispatch('skills_review', {**scope, 'skillId': skill['id'], 'expectedRevision': 4, 'version': 1, 'decision': 'reopen'})


class DeliveryMixin:
    def write_repo(self):
        (self.project / 'CLAUDE.md').write_text('# House rules\n\n' + MARKER + '\n')
        hooks = self.base / 'hook-ran'
        (self.project / '.claude').mkdir(exist_ok=True)
        (self.project / '.claude' / 'settings.json').write_text(json.dumps({'hooks': {'SessionStart': [{'hooks': [
            {'type': 'command', 'command': f'touch {hooks}'}]}], 'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': f'touch {hooks}'}]}]},
            'env': {'GIT_CONFIG_COUNT': '0'}}))
        (self.project / '.mcp.json').write_text(json.dumps({'mcpServers': {'repo': {'command': 'touch', 'args': [str(self.base / 'mcp-ran')]}}}))
        return hooks

    def test_a_repo_claude_md_reaches_the_first_message_and_the_run_record_and_its_hooks_never_run(self):
        hooks = self.write_repo()
        scope = {**self.scope, 'profileId': 'p'}
        rule = self.host.dispatch('knowledge_save', {**scope, 'ruleId': None, 'expectedRevision': 0, 'text': 'USER_RULE_SENTINEL', 'provenance': 'Human', 'source': None})
        self.host.dispatch('knowledge_approve', {**scope, 'ruleId': rule['id'], 'versionId': rule['versions'][0]['id'], 'expectedRevision': 1})
        self.grant()
        with self.peer_patch('none'):
            task = self.request('Inspect the greeting.')['taskId']
            detail = self.settle(task)
        record = detail['task']['runs'][-1]['knowledge_snapshot']['repository']
        self.assertEqual([f['path'] for f in record['files']], ['CLAUDE.md'])
        self.assertEqual(record['files'][0]['sha256'], repo_guidance.read_file(self.project, 'CLAUDE.md')['sha256'])
        first = next(text for text in strings(self.peers[0].sent) if MARKER in text)
        self.assertLess(first.index(MARKER), first.index('USER_RULE_SENTINEL'), 'repository files come before your rules, so yours win')
        self.assertLess(first.index('USER_RULE_SENTINEL'), first.index('Inspect the greeting.'))
        self.assertIn('the user’s saved guidance that follows wins', first)
        self.assertEqual(sum(MARKER in text for text in strings(self.peers[0].sent)), 1, 'sent once, not twice')
        # The repository's configuration never loads: no hook or MCP server ran, and neither CLI was asked to read it.
        self.assertFalse(hooks.exists())
        self.assertFalse((self.base / 'mcp-ran').exists())
        argv = self.peers[0].argv
        if self.provider == 'claude':
            self.assertEqual(argv[argv.index('--setting-sources') + 1], '')
            self.assertIn('--strict-mcp-config', argv)
        else:
            self.assertIn('project_doc_max_bytes=0', ' '.join(argv))
        listed = self.host.dispatch('knowledge_list', {'projectId': 'p', 'channelId': '', 'profileId': 'p'})['repository']['files']
        self.assertFalse(listed[0]['changed'])

    def test_negative_control_without_the_import_the_file_never_reaches_the_provider(self):
        self.write_repo()
        self.grant()
        with patch('repo_guidance.snapshot', return_value=dict(files=[], left_out=[], text='', limit=repo_guidance.LIMIT)), self.peer_patch('none'):
            task = self.request('Inspect the greeting.')['taskId']
            detail = self.settle(task)
        self.assertNotIn(MARKER, json.dumps(self.peers[0].sent))
        self.assertEqual(detail['task']['runs'][-1]['knowledge_snapshot']['repository']['files'], [])

    def test_the_run_record_names_the_notes_it_had(self):
        import project_memory
        db = self.host.store.db
        project_memory.schema(db)
        with db:
            db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', ('n1', 'p', 'personal', 'active',
                       json.dumps(dict(id='n1', text='NOTE_SENTINEL frontend tests run one file at a time.', created_at=1, updated_at=1, provider='codex'))))
        self.grant()
        with self.peer_patch('none'):
            task = self.request('Inspect the greeting.')['taskId']
            detail = self.settle(task)
        run = detail['task']['runs'][-1]
        self.assertEqual([n['id'] for n in run['told']['notes']], ['n1'])
        self.assertIn('NOTE_SENTINEL', json.dumps(self.peers[0].sent))
        listed = self.host.dispatch('tasks', self.scope)
        self.assertNotIn('told', json.dumps(listed), 'the per-run record stays out of the task list')


class CodexRepoDelivery(DeliveryMixin, ConversationCodingTests): pass
class ClaudeRepoDelivery(DeliveryMixin, ConversationCodingTests): provider = 'claude'


def load_tests(loader, tests, pattern):
    import unittest as ut
    suite = ut.TestSuite()
    for cls in (RepoFiles, KnowledgeUndo):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    for cls in (CodexRepoDelivery, ClaudeRepoDelivery):
        for name in ('test_a_repo_claude_md_reaches_the_first_message_and_the_run_record_and_its_hooks_never_run',
                     'test_negative_control_without_the_import_the_file_never_reaches_the_provider',
                     'test_the_run_record_names_the_notes_it_had'):
            suite.addTest(cls(name))
    return suite


if __name__ == '__main__':
    unittest.main()
