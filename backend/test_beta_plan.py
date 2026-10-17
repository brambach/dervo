"""The beta page reads a disposable plan file and a disposable git checkout. No network, no real releases."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from beta_plan import read_plan, view
from dervo_connections import Rejected
from host import Host

PLAN = {'schema': 1, 'next': {'version': '0.1.0-beta.4', 'headline': 'Chats you can point at.',
                              'planned': [{'title': 'Pick a conversation with #', 'detail': 'In the composer.', 'state': 'building'},
                                          {'title': 'Polish the coding experience', 'state': 'planned'}]},
        'shipped': []}


def git(repo, *arguments):
    subprocess.run(['git', '-C', str(repo), *arguments], check=True, capture_output=True,
                   env={'PATH': '/usr/bin:/bin:/usr/local/bin', 'HOME': str(repo),
                        'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 't@example.test',
                        'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 't@example.test'})


class BetaPlanFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'project'; (self.source / 'runtime').mkdir(parents=True)
        (self.source / 'tauri.conf.json').write_text(json.dumps({'identifier': 'local.dervo.connected'}))
        self.host = Host(self.root / 'data'); self.addCleanup(self.host.close)
        self.host.store.add_profile('account', 'codex', self.root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('p', self.source, 'account', 'test-model')
        self.resources = self.root / 'resources'; (self.resources / 'runtime').mkdir(parents=True)
        self.shas = []

    def write(self, plan):
        (self.resources / 'runtime/dervo-beta-plan.json').write_text(json.dumps(plan))

    def commit(self, message):
        if not (self.source / '.git').exists():
            git(self.source, 'init', '-q', '-b', 'main')
        (self.source / 'work.txt').write_text(message)
        git(self.source, 'add', 'work.txt', 'tauri.conf.json')
        git(self.source, 'commit', '-q', '-m', message)
        sha = subprocess.run(['git', '-C', str(self.source), 'rev-parse', '--short', 'HEAD'],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.shas.append(sha)
        return sha


class BetaPlanTests(BetaPlanFixture):
    def test_a_plan_without_a_checkout_still_names_the_versions_and_what_is_next(self):
        self.write({**PLAN, 'shipped': [{'version': '0.1.0-beta.3', 'built': '2026-09-18', 'commit': 'abcdef1',
                                         'headline': 'A fix.'}]})
        result = view(self.host, self.resources)
        self.assertIsNone(result['source'], 'no checkout of Dervo is open in this workspace')
        self.assertEqual(result['next']['version'], '0.1.0-beta.4')
        self.assertEqual([item['state'] for item in result['next']['planned']], ['building', 'planned'])
        self.assertEqual(result['shipped'][0]['headline'], 'A fix.')
        self.assertEqual(result['shipped'][0]['features'], [], 'without git there is nothing to list and nothing invented')
        self.assertIsNone(result['next']['uncommitted'])

    def test_each_version_lists_the_commits_it_was_built_from_and_what_landed_since(self):
        first = self.commit('Baseline')
        shipped_one = self.commit('First run sets Dervo up on a new Mac')
        shipped_two = self.commit('Tasks stopped failing at once')
        self.commit('Pick a conversation with #')
        (self.source / 'unsaved.txt').write_text('editing')
        self.write({**PLAN, 'shipped': [{'version': '0.1.0-beta.3', 'commit': shipped_two, 'built': '2026-09-18'},
                                        {'version': '0.1.0-beta.2', 'commit': shipped_one, 'from': first}]})
        result = view(self.host, self.resources)
        self.assertEqual(result['source'], str(self.source), 'Dervo finds its own source project in this workspace')
        self.assertEqual([row['title'] for row in result['shipped'][0]['features']], ['Tasks stopped failing at once'])
        self.assertEqual([row['title'] for row in result['shipped'][1]['features']], ['First run sets Dervo up on a new Mac'],
                         'a version lists its own commits, not the ones before it')
        self.assertEqual([row['title'] for row in result['next']['landed']], ['Pick a conversation with #'])
        self.assertEqual(result['next']['total'], 1)
        self.assertEqual(result['next']['uncommitted'], 1, 'work in the checkout that no build could contain yet')

    def test_a_plan_that_is_not_a_plan_is_refused_rather_than_shown(self):
        self.write({'schema': 2, 'next': PLAN['next'], 'shipped': []})
        with self.assertRaises(Rejected): read_plan(self.resources)
        self.write({**PLAN, 'shipped': [{'version': '0.1.0-beta.3', 'commit': 'not-a-commit'}]})
        with self.assertRaises(Rejected): read_plan(self.resources)
        self.write({**PLAN, 'next': {'version': '0.1.0-beta.4', 'planned': [{'title': 'x', 'state': 'shipping'}]}})
        with self.assertRaises(Rejected): read_plan(self.resources)
        (self.resources / 'runtime/dervo-beta-plan.json').unlink()
        with self.assertRaises(Rejected): read_plan(self.resources)

    def test_the_operation_takes_no_fields(self):
        self.write(PLAN)
        with self.assertRaises(Rejected): self.host.dispatch('beta_plan', {'projectId': 'p'})

    def test_the_shipped_plan_matches_this_checkout(self):
        """Dervo's own plan file names commits that exist here, so the page it ships with isn't empty."""
        root = Path(__file__).resolve().parent.parent
        if not (root / '.git').exists():
            self.skipTest('not a checkout')
        record = read_plan(root)
        for entry in record['shipped']:
            found = subprocess.run(['git', '-C', str(root), 'cat-file', '-t', entry['commit']],
                                   capture_output=True, text=True)
            self.assertEqual(found.stdout.strip(), 'commit', f"{entry['version']} names {entry['commit']}")


if __name__ == '__main__':
    unittest.main()


class IdeasPlanTests(BetaPlanFixture):
    """18 Sep: what the next release holds follows from the project room. An idea aimed at the next version is a
    planned entry; the file's own list only covers what no idea names."""

    def idea(self, name, state='planned', target='0.1.0-beta.4', summary=''):
        from project_ideas import ProjectIdeas
        ProjectIdeas(self.host.store).dispatch('ideas_save', dict(projectId='p', profileId='account', ideaId=None,
            expectedRevision=0, name=name, summary=summary, state=state, target=target))

    def test_ideas_aimed_at_the_next_version_are_the_plan_and_the_file_fills_the_rest(self):
        self.write(PLAN)
        self.commit('first')
        self.idea('Orchestrator', 'building', summary='The room and the board.')
        self.idea('Pick a conversation with #', 'done')  # the same title as a file entry: the idea wins, once
        self.idea('Memory and skills', target='0.1.0-beta.5')  # another version: not in this plan
        self.idea('No target yet', target=None)
        planned = view(self.host, self.resources)['next']['planned']
        self.assertEqual([(item['title'], item['state'], item['source']) for item in planned],
                         [('Orchestrator', 'building', 'idea'), ('Pick a conversation with #', 'done', 'idea'),
                          ('Polish the coding experience', 'planned', 'file')])
        self.assertEqual(planned[0]['detail'], 'The room and the board.')
        self.assertEqual(planned[0]['conversations'], 0)

    def test_without_dervos_checkout_the_file_is_the_whole_plan(self):
        self.write(PLAN)
        self.idea('Orchestrator', 'building')
        planned = view(self.host, self.resources)['next']['planned']
        self.assertEqual([item['source'] for item in planned], ['file', 'file'], 'no checkout, so no project to read ideas from')

    def test_the_page_is_for_the_dervo_team(self):
        from beta_plan import dispatch
        from types import SimpleNamespace
        self.write(PLAN)
        # A tester's signed-in account is refused; an admin and a source checkout with no account are not.
        self.host.account_session = SimpleNamespace(membership={'allowed': True, 'is_admin': False}, close=lambda: None)
        with self.assertRaisesRegex(Rejected, 'Dervo team'):
            dispatch(self.host, 'beta_plan', {})
        self.host.account_session = SimpleNamespace(membership={'allowed': True, 'is_admin': True}, close=lambda: None)
        from unittest.mock import patch
        with patch('beta_plan.view', lambda host: {'ok': True}):
            self.assertEqual(dispatch(self.host, 'beta_plan', {}), {'ok': True})
            del self.host.account_session
            self.assertEqual(dispatch(self.host, 'beta_plan', {}), {'ok': True})
            self.host.account_session = SimpleNamespace(state='unavailable', close=lambda: None)  # a build with no Dervo sign-in
            self.assertEqual(dispatch(self.host, 'beta_plan', {}), {'ok': True})
        # A signed-out or pending account in a build that has sign-in is not the team.
        self.host.account_session = SimpleNamespace(state='signed_out', membership=None, close=lambda: None)
        with self.assertRaisesRegex(Rejected, 'Dervo team'):
            dispatch(self.host, 'beta_plan', {})


class StampTests(BetaPlanFixture):
    """19 Sep: builds had reached beta.10 while the plan still said beta.3 shipped and beta.4 was next, because the file
    was only ever edited by hand. The release builder now adds each build to the plan it ships with."""

    def work(self, plan):
        work = self.root / 'work'; (work / 'runtime').mkdir(parents=True, exist_ok=True)
        (work / 'runtime/dervo-beta-plan.json').write_text(json.dumps(plan))
        return work

    def test_a_build_becomes_the_newest_version_at_the_source_commit_and_names_the_one_after_it(self):
        from beta_plan import stamp
        head = self.commit('Signed updates')
        work = self.work({**PLAN, 'shipped': [{'version': '0.1.0-beta.3', 'commit': 'abcdef1'}]})
        stamp(work, self.source, '0.1.0-beta.11', '2026-09-19')
        record = read_plan(work)
        self.assertEqual(record['shipped'][0], {'version': '0.1.0-beta.11', 'built': '2026-09-19', 'commit': head, 'headline': None})
        self.assertEqual(record['shipped'][1]['version'], '0.1.0-beta.3')
        self.assertEqual(record['next'], {'version': '0.1.0-beta.12', 'headline': None, 'planned': []},
                         'a plan for beta.4 is stale once beta.11 is built')

    def test_the_plan_for_the_version_being_built_becomes_its_headline(self):
        from beta_plan import stamp
        self.commit('first')
        work = self.work(PLAN)
        stamp(work, self.source, '0.1.0-beta.4', '2026-09-19')
        record = read_plan(work)
        self.assertEqual(record['shipped'][0]['headline'], 'Chats you can point at.')
        self.assertEqual(record['next']['version'], '0.1.0-beta.5')

    def test_a_plan_already_aimed_at_the_next_version_is_kept(self):
        from beta_plan import stamp
        self.commit('first')
        ahead = {**PLAN, 'next': {**PLAN['next'], 'version': '0.1.0-beta.5'}}
        work = self.work(ahead)
        stamp(work, self.source, '0.1.0-beta.4', '2026-09-19')
        self.assertEqual(read_plan(work)['next'], ahead['next'])

    def test_no_plan_file_or_no_commit_leaves_the_snapshot_as_it_was(self):
        from beta_plan import stamp
        empty = self.root / 'empty'; empty.mkdir()
        self.assertIsNone(stamp(empty, self.source, '0.1.0-beta.4', '2026-09-19'))
        work = self.work(PLAN)
        before = (work / 'runtime/dervo-beta-plan.json').read_text()
        self.assertIsNone(stamp(work, self.source, '0.1.0-beta.4', '2026-09-19'), 'the source has no git history yet')
        self.assertEqual((work / 'runtime/dervo-beta-plan.json').read_text(), before)

    def test_a_version_already_listed_is_refused(self):
        from beta_plan import stamp
        self.commit('first')
        work = self.work({**PLAN, 'shipped': [{'version': '0.1.0-beta.4', 'commit': 'abcdef1'}]})
        with self.assertRaises(Rejected): stamp(work, self.source, '0.1.0-beta.4', '2026-09-19')

    def test_a_passed_build_is_saved_back_to_the_source_unless_the_source_changed_meanwhile(self):
        from beta_plan import stamp
        from release_builder import record_build_in_source
        from release_copy import file_hash
        self.commit('first')
        (self.source / 'runtime/dervo-beta-plan.json').write_text(json.dumps(PLAN))
        before = file_hash(self.source / 'runtime/dervo-beta-plan.json')
        work = self.work(PLAN)
        stamp(work, self.source, '0.1.0-beta.4', '2026-09-19')
        self.assertIn('Added', record_build_in_source(self.source, work, before))
        self.assertEqual(read_plan(self.source)['shipped'][0]['version'], '0.1.0-beta.4')
        (self.source / 'runtime/dervo-beta-plan.json').write_text(json.dumps(PLAN))  # edited during the next build
        stamp(work, self.source, '0.1.0-beta.5', '2026-09-19')
        self.assertIn('changed', record_build_in_source(self.source, work, before + '0'))
        self.assertEqual(read_plan(self.source)['shipped'], [], 'the edit made during the build is kept')

    def test_versions_count_on_from_their_last_number(self):
        from beta_plan import successor
        self.assertEqual(successor('0.1.0-beta.10'), '0.1.0-beta.11')
        self.assertEqual(successor('0.1.0-prebeta.9'), '0.1.0-prebeta.10')
        self.assertIsNone(successor('0.1.0-beta'))


class PreviewTests(BetaPlanFixture):
    """20 Sep: every local preview build (0.1.0-beta.11-preview.10) went into shipped and bumped next, so the Releases
    page listed a stack of versions no tester ever had. Previews now keep a list of their own that the page leaves out."""

    work = StampTests.work

    def test_a_preview_build_is_kept_apart_and_leaves_shipped_and_next_alone(self):
        from beta_plan import stamp
        head = self.commit('Signed updates')
        before = {**PLAN, 'shipped': [{'version': '0.1.0-beta.3', 'commit': 'abcdef1'}]}
        work = self.work(before)
        stamp(work, self.source, '0.1.0-beta.4-preview.1', '2026-09-20')
        record = read_plan(work)
        self.assertEqual(record['shipped'], before['shipped'])
        self.assertEqual(record['next'], PLAN['next'], 'a preview never bumps next')
        self.assertEqual(record['previews'], [{'version': '0.1.0-beta.4-preview.1', 'built': '2026-09-20', 'commit': head}])
        stamp(work, self.source, '0.1.0-beta.4-preview.2', '2026-09-20')
        self.assertEqual([entry['version'] for entry in read_plan(work)['previews']],
                         ['0.1.0-beta.4-preview.2', '0.1.0-beta.4-preview.1'])
        with self.assertRaises(Rejected): stamp(work, self.source, '0.1.0-beta.4-preview.2', '2026-09-20')
        # Negative control: the real version in the same plan does go to shipped and does move next on.
        stamp(work, self.source, '0.1.0-beta.4', '2026-09-20')
        record = read_plan(work)
        self.assertEqual([entry['version'] for entry in record['shipped']], ['0.1.0-beta.4', '0.1.0-beta.3'])
        self.assertEqual(record['next']['version'], '0.1.0-beta.5')
        self.assertEqual(len(record['previews']), 2)

    def test_a_plan_written_by_the_old_builder_is_cleaned_on_the_next_build(self):
        from beta_plan import stamp
        self.commit('first')
        old = {**PLAN, 'next': {'version': '0.1.0-beta.11-preview.6', 'headline': None, 'planned': []},
               'shipped': [{'version': '0.1.0-beta.11-preview.5', 'commit': 'ec58452', 'built': '2026-09-19', 'headline': None},
                           {'version': '0.1.0-beta.3', 'commit': 'dcb3a65', 'headline': 'A fix.'}]}
        work = self.work(old)
        stamp(work, self.source, '0.1.0-beta.11-preview.10', '2026-09-20')
        record = read_plan(work)
        self.assertEqual([entry['version'] for entry in record['shipped']], ['0.1.0-beta.3'])
        self.assertEqual([entry['version'] for entry in record['previews']],
                         ['0.1.0-beta.11-preview.10', '0.1.0-beta.11-preview.5'])
        self.assertEqual(record['next']['version'], '0.1.0-beta.11', 'next is the release the previews were building towards')
        # When that release has already shipped, next is the one after it.
        work = self.work({**old, 'shipped': old['shipped'] + [{'version': '0.1.0-beta.11', 'commit': 'abcdef1'}]})
        stamp(work, self.source, '0.1.0-beta.12-preview.1', '2026-09-20')
        self.assertEqual(read_plan(work)['next']['version'], '0.1.0-beta.12')

    def test_the_page_leaves_previews_out_even_before_the_file_is_cleaned(self):
        self.write({**PLAN, 'next': {'version': '0.1.0-beta.11-preview.6', 'headline': None, 'planned': []},
                    'shipped': [{'version': '0.1.0-beta.11-preview.5', 'commit': 'ec58452'},
                                {'version': '0.1.0-beta.3', 'commit': 'dcb3a65'}],
                    'previews': [{'version': '0.1.0-beta.11-preview.4', 'commit': 'ccb93b6'}]})
        result = view(self.host, self.resources)
        self.assertEqual([entry['version'] for entry in result['shipped']], ['0.1.0-beta.3'], 'the real version is still shown')
        self.assertEqual(result['next']['version'], '0.1.0-beta.11')
        self.assertNotIn('preview', json.dumps(result))

    def test_a_malformed_preview_list_is_refused(self):
        self.write({**PLAN, 'previews': [{'version': '0.1.0-beta.4-preview.1', 'commit': 'nope'}]})
        with self.assertRaises(Rejected): read_plan(self.resources)
        self.write({**PLAN, 'previews': {}})
        with self.assertRaises(Rejected): read_plan(self.resources)


class BuilderPlanTests(unittest.TestCase):
    """The builder writes the source plan only once a build has passed every step."""

    def setUp(self):
        import test_release_storage as storage_tests
        storage_tests.StorageTests.setUp(self)
        self.fake_command = storage_tests.StorageTests.fake_command.__get__(self)
        (self.source / 'runtime').mkdir(exist_ok=True)
        self.plan = self.source / 'runtime/dervo-beta-plan.json'
        self.plan.write_text(json.dumps({**PLAN, 'shipped': [{'version': '0.1.0-beta.3', 'commit': 'abcdef1'}]}))
        git(self.source, 'init', '-q', '-b', 'main')
        git(self.source, 'add', '-A')
        git(self.source, 'commit', '-q', '-m', 'first')

    def build(self, release_id, fail_at=None):
        import sys
        from unittest.mock import patch
        from release_builder import build
        real = subprocess.run
        def run(command, **kwargs):
            if Path(str(command[0])).name == 'git':
                return real(command, **kwargs)
            if fail_at and fail_at(command):
                return subprocess.CompletedProcess(command, 1)
            return self.fake_command(command, **kwargs)
        with patch('release_builder.BuildStorage', return_value=self.storage), \
             patch('release_builder.shutil.which', return_value=sys.executable), \
             patch('release_builder.subprocess.run', side_effect=run):
            return build(self.source, self.root / release_id, {})

    def test_a_failed_build_never_writes_the_plan(self):
        before = self.plan.read_bytes()
        for step in ('feature-checks', 'native-bundle'):
            fail = (lambda command: 'release_checks.py' in command) if step == 'feature-checks' \
                else (lambda command: '--bundles' in command)
            with self.assertRaisesRegex(RuntimeError, step + ' failed'):
                self.build('0.1.0-beta.4-' + step, fail_at=fail)
            self.assertEqual(self.plan.read_bytes(), before, step)
        # The last steps: signing passes, then the bundle's executable is gone when the builder reads its hash. The old
        # builder had already copied the plan to the source by then.
        from unittest.mock import patch
        def sign(bundle, **kwargs):
            (bundle / 'Contents/MacOS/dervo-local').unlink()
            return {'mode': 'ad-hoc', 'detail': 'Signed ad hoc.'}
        with patch('release_signing.sign', sign), self.assertRaises(OSError):
            self.build('0.1.0-beta.4-late')
        self.assertEqual(json.loads((self.root / '0.1.0-beta.4-late/release.json').read_text())['state'], 'failed')
        self.assertEqual(self.plan.read_bytes(), before, 'a failure after signing')
        # Negative control: the same build passing does write it.
        state = self.build('0.1.0-beta.4')
        self.assertEqual(state['state'], 'built')
        self.assertIn('Added', state['beta_plan'])
        self.assertEqual(read_plan(self.source)['shipped'][0]['version'], '0.1.0-beta.4')

    def test_a_passed_preview_build_lands_in_previews_not_shipped(self):
        state = self.build('0.1.0-beta.4-preview.1')
        self.assertEqual(state['state'], 'built')
        record = read_plan(self.source)
        self.assertEqual([entry['version'] for entry in record['shipped']], ['0.1.0-beta.3'])
        self.assertEqual(record['next']['version'], '0.1.0-beta.4')
        self.assertEqual([entry['version'] for entry in record['previews']], ['0.1.0-beta.4-preview.1'])
