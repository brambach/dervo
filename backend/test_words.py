"""Word matching for skills, rule order, history search and the near-duplicate check, plus keeping the right notes
when memory fills. The cases are the no-model part of the smartness review's eval sketch
(notes/smartness/memory-knowledge-skills.md, items 2 to 4; eval cases 6, 8, 9, 12, 14, 15 and 16). Every table has
rows whose right answer is "no": those are the negative controls. No provider, no network, temporary folders only."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import project_memory
import project_skills
import words
from project_knowledge import order_for_request
from test_project_memory import Coding, task, remember
from test_project_skills import SkillFixture


# ------------------------------------------------------------------ labelled tables (also read by the before/after count)
SKILLS = {
    'cut-a-release': 'Cutting a new release build of the Mac app.',
    'add-a-bridge-operation': 'Adding an operation the renderer can call through the bridge.',
    'wire-a-screen': 'Adding a new screen reached from the sidebar.',
    'run-the-web-tests': 'Running the frontend test suite one file at a time.',
}
# (request, the skills that should go with it). Every other skill for that request should not.
SKILL_CASES = [
    ('cut a release', {'cut-a-release'}),
    ('release the app', {'cut-a-release'}),
    ('cutting releases this afternoon, walk me through it', {'cut-a-release'}),
    ('releases', {'cut-a-release'}),
    ('we need to publish beta 12 for testers', {'cut-a-release'}),  # a synonym (words.SYNONYM_ROWS)
    ("let's ship beta 12 tonight", {'cut-a-release'}),
    ('please verify the web tests pass', {'run-the-web-tests'}),
    ('cut the sidebar padding in half', set()),  # "cut" is in the name, so it's only one shared word
    ('release the lock on the database file', set()),
    ('check the release notes for typos', set()),
    ('the ship date is in the release notes', set()),  # nouns, not verbs
    ('build the release notes page', set()),
    ('make a new build', set()),
    ('The release notes have a typo', set()),
    ('add a bridge operation and dispatch it in the host', {'add-a-bridge-operation'}),
    ('wire a new screen into the sidebar', {'wire-a-screen'}),
    ('the web tests fail when they run together', {'run-the-web-tests'}),
    ('fix the header colour on the settings screen', set()),
    ('rename the bridge crate', set()),
]
KNOWN_MISS = set()

# (note already saved, note being saved, same?). "Same" includes a contradiction of the saved note: the agent is
# steered to replace it, which is what we want.
DUPLICATE_CASES = [
    ('The web tests need Node 20.', 'The web tests need the dev server running.', False),
    ('The web tests need the dev server running.', 'The web tests need Node 20 and pnpm.', False),
    ('The app is signed with the team key from the keychain.', 'The app is notarised with the team key from the keychain.', False),
    ('The docs site is in the website folder.', 'The docs site is built from the main branch.', False),
    ('Bryce wants the Home composer to stay one line.', 'Bryce wants the Home reply to stay under three lines.', False),
    ('Tests run with python3 -m unittest from backend.', 'Tests run one file at a time.', False),
    ('The user wants plain words in the UI.', 'The user wants short commit titles.', False),
    ('The API is in the backend folder.', 'The API is slow on the first call after launch.', False),
    ('Deploys go through the staging box first.', 'Deploys go through the staging box first, every time.', True),
    ('The deploy script lives in scripts/deploy.sh and needs the staging flag.',
     'The deploy script lives in scripts/deploy.sh and it needs the staging flag.', True),
    ('The API uses port 8080.', 'The API no longer uses port 8080; it uses 9090.', True),
    ('The app is built with Tauri and the frontend with Vite.', 'The frontend is built with Vite and the app with Tauri.', True),
    ('Release notes go in CHANGES.md.', 'Release notes go in CHANGES.md at the top.', True),
    ('The sidebar uses the old icon set.', 'The sidebar uses the new icon set from the design kit.', True),
]
# Two facts that differ in one word are beyond word overlap: expected to stay a false refusal.
KNOWN_DUPLICATE_MISS = {('The app is signed with the team key from the keychain.',
                         'The app is notarised with the team key from the keychain.')}

RULES = [
    dict(id='general', text='Keep changes inside the granted files.'),
    dict(id='release', text='Releases are cut from the main branch only.'),
    dict(id='search', text='Search queries are kept in sessionStorage so a reload keeps them.'),
    dict(id='icons', text='Icons come from the shared icon set, never inline SVG.'),
]
# (request, the rule that should come first)
RULE_CASES = [
    ('cutting a release tonight', 'release'),
    ('releasing on Friday', 'release'),
    ('the search query box loses its text', 'search'),
    ('swap the inline svg for a proper icon', 'icons'),
    ('migrate the database to the new schema', 'general'),  # nothing in common: the saved order stands
    ('ok', 'general'),
]


def skill_rows(matches):
    live = [dict(name=name, description=description) for name, description in SKILLS.items()]
    rows = []
    for request, wanted in SKILL_CASES:
        sent = {skill['name'] for skill in matches(live, request)}
        rows += [(request, name, name in sent, name in wanted) for name in SKILLS]
    return rows


def duplicate_rows(overlap, bar):
    return [(a, b, overlap(a, b) >= bar, same) for a, b, same in DUPLICATE_CASES]


def rule_rows(order):
    return [(query, order(RULES, query)[0]['id'], first) for query, first in RULE_CASES]


class WordFormsTests(unittest.TestCase):
    def test_word_forms_fold_together_and_filler_is_dropped(self):
        self.assertEqual(words.terms('cutting releases'), words.terms('cut a release'))
        self.assertEqual(words.stem('running'), words.stem('run'))
        self.assertIn(words.stem('workspace'), words.terms('WorkspaceSearch'))
        self.assertEqual(words.terms('the and of to 2026'), set())
        # Negative controls: different words stay different, and a short word isn't cut down to nothing.
        self.assertNotEqual(words.stem('release'), words.stem('relay'))
        self.assertEqual(words.stem('css'), 'css')
        self.assertEqual(words.stem('bus'), 'bus')

    def test_the_fold_matches_home_t1s_so_project_ideas_can_switch_to_it(self):
        # b11/home-t1 073a62a: project_ideas.stem and word_forms. Same inputs, same outputs.
        for word, folded in (('crashes', 'crash'), ('stopped', 'stop'), ('running', 'run'), ('libraries', 'library'),
                             ('boxes', 'box'), ("bryce's", 'bryc'), ('status', 'status'), ('analysis', 'analysis')):
            self.assertEqual(words.stem(word), folded, word)
        self.assertEqual(words.word_forms('The crashes', {'the'}), {'crash'})


class SkillMatchTests(unittest.TestCase):
    def test_labelled_requests_get_the_right_skills(self):
        wrong = [(request, name) for request, name, sent, wanted in skill_rows(project_skills.matches) if sent != wanted]
        self.assertEqual(set(wrong), KNOWN_MISS, 'nothing is wrong')
        self.assertEqual(len(skill_rows(project_skills.matches)), 76)

    def test_a_skill_needs_a_word_from_its_name(self):
        live = [dict(name='cut-a-release', description='Signing and notarising the Mac build with the team key.')]
        self.assertEqual(project_skills.matches(live, 'rotate the team key used for signing'), [],
                         'two words from the description alone is someone else\'s subject')
        # Negative control: one of them in the name, and it's sent.
        self.assertEqual([s['name'] for s in project_skills.matches(live, 'cut a release and check the signing')], ['cut-a-release'])


class SynonymTests(unittest.TestCase):
    def test_a_dev_verb_counts_only_as_a_verb(self):
        self.assertEqual(words.verb_actions('we need to publish beta 12'), {'release': {'publish'}})
        self.assertEqual(words.verb_actions('Rolling out to staging, then verify it'), {'deploy': {'rollout'}, 'test': {'verify'}})
        self.assertEqual(words.verb_actions('please repair it and drop the table'), {'fix': {'repair'}, 'remove': {'drop'}})
        # Negative controls: the same words as nouns or modifiers, and words outside the table.
        self.assertEqual(words.verb_actions('build the release notes page'), {})
        self.assertEqual(words.verb_actions('the ship date moved'), {})
        self.assertEqual(words.verb_actions('rename the bridge crate'), {})

    def test_the_table_is_only_read_by_skill_matching(self):
        # Rule order, history search and the duplicate check keep plain word forms: "publish" is not "release" there.
        self.assertNotEqual(words.terms('publish beta 12'), words.terms('release beta 12'))
        self.assertEqual(order_for_request(RULES, 'publish on Friday')[0]['id'], 'general')

    def test_a_synonym_stands_for_the_skills_name_but_not_for_another_subject(self):
        live = [dict(name='deploy-to-staging', description='Deploying the web app to the staging box.'),
                dict(name='cut-a-release', description='Cutting a new release build of the Mac app.')]
        sent = lambda request: [s['name'] for s in project_skills.matches(live, request)]
        self.assertEqual(sent('roll out to staging'), ['deploy-to-staging'])
        self.assertEqual(sent('we need to publish beta 12 for testers'), ['cut-a-release'])
        # Negative controls: the verb is literal in the name, used as a noun, or another action's verb.
        self.assertEqual(sent('cut the sidebar padding'), [])
        self.assertEqual(sent('build the release notes page'), [])
        self.assertEqual(sent('check the staging logs'), [], 'check is a test verb, not a deploy one')
        self.assertEqual(sent('publish the app'), ['cut-a-release'])
        self.assertEqual(sent("let's ship beta 12"), ['cut-a-release'])
        # Negative controls: a lone verb says an action, not this skill's subject (review of m13, 20 Sep).
        for request in ('publish the blog post', 'ship the new onboarding copy', 'publish to the npm registry',
                        'ship the release notes', 'ship it'):
            self.assertEqual(sent(request), [], request)


class OkGoTests(SkillFixture):
    """Eval case 15: a short follow-up in a fresh session is about what the chat was already doing."""
    def test_a_short_follow_up_uses_the_chats_earlier_goal(self):
        self.save(name='cut-a-release', description='Cutting a new release build of the Mac app.', body='1. Build.\n2. Sign.')
        self.task('t1', 'can we cut a release?', state='answered',
                  runs=[dict(id='r-t1', state='completed', guide=dict(goal='Cut a release of the Mac app'))])
        block = project_skills.context_block(self.coding, self.task('t2', 'ok go'))
        self.assertIn('### cut-a-release', block)

    def test_negative_controls_nothing_earlier_another_chat_or_a_real_request(self):
        self.save(name='cut-a-release', description='Cutting a new release build of the Mac app.', body='1. Build.\n2. Sign.')
        self.assertEqual(project_skills.context_block(self.coding, self.task('t1', 'ok go')), '', 'nothing earlier in this chat')
        self.task('t2', 'can we cut a release?', thread='other', state='answered')
        self.assertEqual(project_skills.context_block(self.coding, self.task('t3', 'ok go')), '', 'another chat\'s goal is not this one\'s')
        self.task('t4', 'can we cut a release?', thread='d', state='answered')
        self.assertEqual(project_skills.context_block(self.coding, self.task('t5', 'rename the settings screen', thread='d')), '',
                         'a message with its own subject is judged on its own words')


class RuleOrderTests(unittest.TestCase):
    def test_labelled_requests_put_the_right_rule_first(self):
        wrong = [(query, got, want) for query, got, want in rule_rows(order_for_request) if got != want]
        self.assertEqual(wrong, [])


class DuplicateTests(unittest.TestCase):
    def test_labelled_note_pairs(self):
        wrong = {(a, b) for a, b, refused, same in duplicate_rows(project_memory._overlap, project_memory.NEAR_DUPLICATE) if refused != same}
        self.assertEqual(wrong, KNOWN_DUPLICATE_MISS)

    def test_eval_case_6_both_facts_stay(self):
        coding = Coding()
        owner = task(coding, 'one')
        first = remember(coding, owner, text='The web tests need Node 20.')
        second = remember(coding, owner, text='The web tests need the dev server running.')
        self.assertTrue(second['saved'])
        self.assertEqual({e['text'] for e in project_memory.entries(coding.store.db, 'p', 'personal')},
                         {'The web tests need Node 20.', 'The web tests need the dev server running.'})
        # Negative control: the same fact in other words is still refused, naming the note it matched.
        again = remember(coding, owner, text='The web tests need Node 20 installed.')
        self.assertEqual((again['saved'], again['entry_id']), (False, first['entry_id']))


class HistorySearchTests(unittest.TestCase):
    """Eval case 16, no model: the chat that made the switch ranks first for a question about it."""
    def setUp(self):
        self.coding = Coding()
        now = int(time.time())
        task(self.coding, 'npm', thread='a', state='answered', at=now - 5000, text='Switch the project from npm to pnpm.',
             answer='Moved to pnpm because workspace hoisting broke.')
        for n, text in enumerate(('Move the sidebar toggle to the right.', 'Moved the settings page under Account.',
                                  'Move the tests into backend.')):
            task(self.coding, f'move{n}', thread=f'm{n}', state='answered', at=now - 100 + n, text=text, answer='Done.')
        self.me = task(self.coding, 'me', thread='c')

    def ids(self, query):
        found = project_memory.search(self.coding, self.me, {'query': query})
        return [r['asked'] for r in found['results']]

    def test_the_rare_word_wins_over_a_common_one(self):
        self.assertEqual(self.ids('why did we move off npm?')[0], 'Switch the project from npm to pnpm.')
        # Negative control: a question about the sidebar finds the sidebar chat first, not the npm one.
        self.assertEqual(self.ids('where does the sidebar toggle live?')[0], 'Move the sidebar toggle to the right.')

    def test_the_chats_reported_goal_is_searched(self):
        record = task(self.coding, 'goal', thread='g', state='answered', text='ok go', answer='Done.')
        record['runs'][-1]['guide'] = dict(goal='Rename the billing webhook handler')
        self.coding.save(record)
        self.assertIn('ok go', self.ids('billing webhook'))
        # Negative control: words from nowhere in any chat find nothing.
        self.assertEqual(self.ids('kubernetes helm chart'), [])


class MemoryFullTests(unittest.TestCase):
    """Eval case 9: memory is full, the first note was corrected an hour ago, and one more is saved."""
    def fill(self, coding):
        db = coding.store.db
        project_memory.schema(db)
        written = int(time.time()) - 86400
        with db:
            for n in range(project_memory.MAX_ENTRIES):
                item = dict(id=f'f{n:02d}', text=f'Filler fact {n} about component{n}.', created_at=written, updated_at=written,
                            provider='claude', source=dict(thread_id='c', task_id='old', run_id='r', visibility='shared'))
                db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', (item['id'], 'p', 'personal', 'active', json.dumps(item)))

    def status(self, coding):
        return {row[0]: row[1] for row in coding.store.db.execute('SELECT id, status FROM project_memory')}

    def test_a_note_replaced_recently_outlives_one_nobody_touched(self):
        coding = Coding(); self.fill(coding)
        owner = task(coding, 'one')
        corrected = remember(coding, owner, 'replace', 'Filler fact 0, corrected: component0 lives in src/zero.', 'f00')
        saved = remember(coding, coding.read('one'), text='The header uses the brand blue.')
        # The correction is a new row that took f00's place (round 4), so it is the freshest note and f01 goes.
        self.assertEqual((self.status(coding)['f00'], self.status(coding)[corrected['entry_id']], self.status(coding)['f01']),
                         ('superseded', 'active', 'removed'))
        # Since beta.16 full memory merges before it retires by age, and the fillers differ only by a number, so f01 goes
        # as merged into a newer filler. Which note goes still follows who was written last.
        self.assertIn('retired to make room: "Filler fact 1 about component1."', saved['detail'])
        self.assertIn('the person can bring them back', saved['detail'])
        # Negative control: with nothing replaced, the first note goes, as before.
        coding = Coding(); self.fill(coding)
        remember(coding, task(coding, 'one'), text='The header uses the brand blue.')
        self.assertEqual((self.status(coding)['f00'], self.status(coding)['f01']), ('removed', 'active'))

    def test_a_note_an_agent_tried_to_save_again_counts_as_fresh(self):
        coding = Coding(); self.fill(coding)
        owner = task(coding, 'one')
        self.assertFalse(remember(coding, owner, text='Filler fact 0 about component0.')['saved'])
        remember(coding, coding.read('one'), text='The header uses the brand blue.')
        self.assertEqual((self.status(coding)['f00'], self.status(coding)['f01']), ('active', 'removed'))


class StalePathTests(unittest.TestCase):
    """Eval case 8, no model: the agent is told when a note names a path the project folder no longer has."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        (self.folder / 'scripts').mkdir(); (self.folder / 'scripts/deploy.sh').write_text('x')
        (self.folder / 'feature').mkdir()
        self.coding = Coding()
        remember(self.coding, task(self.coding, 'seed'), text='The deploy script is scripts/deploy.sh.')
        self.coding.save(dict(self.coding.read('seed'), state='idle'))

    def block(self, id, **extra):
        record = task(self.coding, id, **extra)
        record['binding']['cwd'] = str(self.folder)
        self.coding.save(record)
        return project_memory.context_block(self.coding, record)

    def test_a_moved_file_is_flagged_and_a_continued_session_hears_about_it(self):
        block = self.block('first')
        self.assertIn('The deploy script is scripts/deploy.sh.', block)
        self.assertNotIn('isn’t in the project folder', block, 'negative control: the file is there')
        (self.folder / 'bin').mkdir(); (self.folder / 'scripts/deploy.sh').rename(self.folder / 'bin/deploy.sh')
        block = self.block('second', continuity=dict(state='continues', from_task_id='first'))
        self.assertIn('scripts/deploy.sh. (names scripts/deploy.sh; Dervo finds no file with that name anywhere in the project folder now. '
                      'Unless a file you read shows otherwise, say so in your answer and correct this note with dervo_remember', block)
        self.assertEqual(self.block('third', continuity=dict(state='continues', from_task_id='second')), '',
                         'nothing changed since, so nothing is sent again')
        self.assertEqual(project_memory.entries(self.coding.store.db, 'p', 'personal')[0]['text'],
                         'The deploy script is scripts/deploy.sh.', 'the note itself is unchanged')

    def test_slashes_that_are_not_paths_are_not_judged(self):
        missing = project_memory.missing_paths(
            'Use and/or in copy, quote AUD/USD rates, name branches feature/short-name, and legacy code was in old/router.ts.',
            str(self.folder))
        self.assertEqual(missing, ['feature/short-name', 'old/router.ts'],
                         'judged: a path under a folder the project has, or one ending in a file name')


if __name__ == '__main__':
    unittest.main()


class NumberVariantTests(unittest.TestCase):
    def test_a_note_that_changes_only_numbers_is_a_variant(self):
        """Critical review, 20 Sep, #1: content_words drops every token of two characters or fewer, so "Node 20" and
        "Node 22" scored 1.0 and the correction was refused. The number is the axis the overlap check can't see. A
        variant is a hint to the agent, which names the note it corrects; the words never retire a row."""
        for old, new, want in (
            ('The web tests need Node 20.', 'The web tests need Node 22.', [('20', '22')]),
            ('The tests need Python 3.9.', 'The tests need Python 3.11.', [('3.9', '3.11')]),
            ('The API uses port 8080.', 'The API uses port 9090.', [('8080', '9090')]),
            ('Builds target macOS 14.', 'builds target macOS 15', [('14', '15')]),
            ('The app ships as v2.', 'The app ships as v3.', [('v2', 'v3')]),
            ('Tests need Node 20 and pnpm 9.', 'Tests need Node 22 and pnpm 9.', [('20', '22')]),
            ('The app pins Gunicorn 21.', 'The app pins Gunicorn 23.', [('21', '23')]),
            # Hints, not judgements: an enumeration, a downgrade and a decimal are variants too, and harmless as hints.
            ('Agent 1 runs the tests.', 'Agent 2 runs the tests.', [('1', '2')]),
            ('The web tests need Node 22.', 'The web tests need Node 18.', [('22', '18')]),
            ('Coverage sits at 82.5 percent.', 'Coverage sits at 91.3 percent.', [('82.5', '91.3')]),
        ):
            self.assertEqual(words.number_variant(old, new), want, (old, new))

    def test_what_is_not_a_variant(self):
        for old, new in (
            ('The web tests need Node 20.', 'The web build needs Node 22.'),
            ('The web tests need Node 20.', 'The build needs Node 22.'),
            ('The web tests need Node 20.', 'The web tests need Node 20 installed.'),
            ('The API uses port 8080.', 'The API uses port 8080.'),
            ('The API uses port 8080.', 'The API no longer uses port 8080; it uses 9090.'),
            ('The project uses pnpm as its package manager.', 'The project uses npm as its package manager.'),
            ('', 'The API uses port 9090.'),
            # Digits that aren't ASCII.
            ('The web tests need Node 20.', 'The web tests need Node \uff12\uff12.'),
            ('The web tests need Node 20.', 'The web tests need Node \u0662\u0662.'),
        ):
            self.assertEqual(words.number_variant(old, new), [], (old, new))
