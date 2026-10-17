"""Auto-sorting: filing, names, workstreams, Needs you. From notes/smartness/auto-sorting.md, section 2.

Rules cases need no model: a thread's first message goes in through ``queue`` and the rules name and file it. Fake
cases use the fake CLI's result text. Real cases are Home's own Sonnet call (one per send or arrival).
"""
from evals.case import REGISTRY, case
from evals.harness import FAKE_MODEL, has, has_any

PICKER = 'the connector picker should warn when a server isn’t allowed in this project'
LOGIN = 'Fix login: the session expires after an hour and people have to sign in again'
SIX = ['Fix the key handling in the composer', 'Show token usage per thread in the sidebar', 'Make the sidebar 20% narrower',
       'Support Cmd+K for the command palette', 'Fix the sidebar/composer gap', 'Add a #mention picker to the composer']
NINE = ['Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped, with the idea as the group key for conversations.',
        'Ideally I want to see this thing working and see the orchestrator working in real time so I can tell what each job is doing',
        'I think it would be good to do a full design sweep as a really critical design person who is looking at every screen',
        PICKER, 'Tighten the sidebar spacing between groups and make the selected row frosted', LOGIN,
        'After a beta downloads, offer Update and restart in the sidebar footer instead of a dialog',
        'Add the release planner screen for the owner: four lists, in this beta, built for the next beta, people want, planned',
        'Knowledge page feels scattered, make it one clear page with guidance, lessons and starter procedures']


def rules_project(h, ctx):
    """A project on the fake profile: nothing here ever calls a model."""
    ctx['ids'] = h.project(h.fake_profile(), FAKE_MODEL)
    return ctx['ids']


def typed(h, ctx, text):
    """A thread you open and type into."""
    thread = h.thread(ctx['ids'], '', automatic=True)
    h.message(ctx['ids'], thread, text)
    return thread


def home_project(h, ctx):
    """A project whose account is Home's (real Claude in real mode); seed threads bind to the fake profile."""
    profile, model = h.agent('claude')
    ctx['ids'] = h.project(profile, model)
    ctx['fake'] = h.fake_profile()
    return ctx['ids']


def seed_thread(h, ctx, name, text, answer, goal=None):
    """A shared thread with one scripted exchange, so Home's snapshot has a brief for it."""
    ids = ctx['ids']
    thread = h.thread(ids, name, profile=ctx['fake'])
    h.grant(ids, thread=thread)
    h.scripted(ids, thread, text, result_text=answer, guide=dict(goal=goal) if goal else None)
    return thread


@case('s1-hand-move-beats-topic', 'sorting', 'Your hand move into Preview pane beats the Design topic guess',
      source='auto-sorting #1, probed')
def _():
    def setup(h, ctx):
        ids = rules_project(h, ctx)
        h.workstream(ids, 'Preview pane')
        first = typed(h, ctx, 'Capture a screenshot of the running app')
        h.file_by_hand(ids, first, 'Preview pane')

    def action(h, ctx):
        thread = typed(h, ctx, 'Tighten the screenshot typography')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        if org['group'] != 'Preview pane':
            return False, f"filed under {org['group']!r}, not Preview pane"
        return True, f"filed under Preview pane as {org['name']!r}"
    return setup, action, check


@case('s2-this-is-not-a-follow-up', 'sorting', '"This button is misaligned in settings" is not a follow-up to Fix login',
      source='auto-sorting #1, probed')
def _():
    def setup(h, ctx):
        rules_project(h, ctx)
        typed(h, ctx, PICKER)
        ctx['login'] = typed(h, ctx, LOGIN)

    def action(h, ctx):
        thread = typed(h, ctx, 'This button is misaligned in settings')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        login = h.organisation(ctx['ids'], ctx['login'])['group']
        if org['group'] and org['group'] == login:
            return False, f"joined {login!r}, the workstream whose task moved last"
        return True, f"its own workstream {org['group']!r} (login is {login!r})"
    return setup, action, check


@case('s3-follow-up-follows-open-thread', 'sorting', '"also show which server refused" joins the picker thread you just had open',
      source='auto-sorting #1')
def _():
    def setup(h, ctx):
        ids = rules_project(h, ctx)
        ctx['picker'] = typed(h, ctx, PICKER)
        ctx['login'] = typed(h, ctx, LOGIN)
        h.call('conversation_seen', projectId=ids.project, channelId=ctx['picker'])

    def action(h, ctx):
        thread = typed(h, ctx, 'also show which server refused, right in the warning')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        picker = h.organisation(ctx['ids'], ctx['picker'])['group']
        if org['group'] != picker:
            return False, f"filed under {org['group']!r}; the picker thread is in {picker!r}"
        return True, f'joined {picker!r}'
    return setup, action, check


@case('s4-everyday-words-get-names', 'sorting', 'Six ordinary requests with key, token, %, +, / and # each get a name and a workstream',
      source='auto-sorting #2, probed')
def _():
    def setup(h, ctx):
        rules_project(h, ctx)

    def action(h, ctx):
        return {text: h.organisation(ctx['ids'], typed(h, ctx, text)) for text in SIX}

    def check(h, ctx, orgs):
        unnamed = [t for t, o in orgs.items() if o['name'] in ('', 'New conversation') or not o['group']]
        if unnamed:
            return False, f'{len(unnamed)} of 6 left unnamed or unfiled: ' + '; '.join(u[:32] for u in unnamed)
        return True, 'all six named and filed: ' + ', '.join(o['name'] for o in orgs.values())
    return setup, action, check


@case('s4b-secrets-stay-out-of-names', 'sorting', 'A pasted key or password never reaches a thread name', source='auto-sorting #2 controls')
def _():
    texts = ['the API key is sk-proj-AbC123xyz789', 'the password is hunter2', 'set token ghp_abc123def456ghi789 for the upload job']

    def setup(h, ctx):
        rules_project(h, ctx)

    def action(h, ctx):
        return [h.organisation(ctx['ids'], typed(h, ctx, t))['name'] for t in texts]

    def check(h, ctx, names):
        leaked = [n for n in names if has_any(n, 'AbC123xyz789', 'hunter2', 'ghp_abc123')]
        return (not leaked), ('leaked: ' + ', '.join(leaked)) if leaked else f'names {names}'
    return setup, action, check


@case('s5-workstream-names-are-nouns', 'sorting', 'Catch up, CSV export or Invoices, and Home (holding both Home threads)',
      source='auto-sorting #3, probed')
def _():
    def setup(h, ctx):
        rules_project(h, ctx)

    def action(h, ctx):
        ids = ctx['ids']
        groups = {}
        groups['catch'] = h.organisation(ids, typed(h, ctx, 'Improve the catch up screen'))['group']
        groups['csv'] = h.organisation(ids, typed(h, ctx, 'Crash when exporting invoices to CSV'))['group']
        groups['home1'] = h.organisation(ids, typed(h, ctx, 'Home keeps filing threads under the wrong workstream'))['group']
        groups['home2'] = h.organisation(ids, typed(h, ctx, 'The Home summary repeats itself'))['group']
        return groups

    def check(h, ctx, g):
        wrong = []
        if (g['catch'] or '').casefold() != 'catch up':
            wrong.append(f"catch up thread: {g['catch']!r}")
        if (g['csv'] or '').casefold() not in ('csv export', 'invoices', 'invoice export'):
            wrong.append(f"csv thread: {g['csv']!r}")
        if not (g['home1'] and g['home1'] == g['home2'] and g['home1'].casefold() == 'home'):
            wrong.append(f"home threads: {g['home1']!r} and {g['home2']!r}")
        return (not wrong), ('; '.join(wrong) if wrong else 'Catch up, CSV export, Home')
    return setup, action, check


@case('s6-empty-rules-workstream-holds-no-slot', 'sorting', 'Six of yours plus one empty rules-made workstream still leave room for a new one',
      source='auto-sorting #3')
def _():
    names = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot']

    def setup(h, ctx):
        ids = rules_project(h, ctx)
        for name in names:
            h.workstream(ids, name)
        # A rules-made workstream, then its one thread moved out by hand (the sidebar's Move, ideas_assign). Before
        # b11/sorting-t1 the emptied workstream stayed listed and kept its slot; now it's archived as 'emptied'.
        thread = typed(h, ctx, 'Fix login: the session expires after an hour')
        ctx['rules_made'] = h.organisation(ids, thread)['group']
        h.file_by_hand(ids, thread, 'Alpha')

    def action(h, ctx):
        thread = typed(h, ctx, 'Add usage meters for Codex accounts')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        taken = set(names) | {ctx['rules_made']}
        if not org['group'] or org['group'] in taken:
            return False, f"joined {org['group']!r} instead of getting its own (empty rules workstream {ctx['rules_made']!r} counts toward the cap)"
        return True, f"new workstream {org['group']!r}"
    return setup, action, check


@case('s6b-archived-thread-frees-rules-workstream', 'sorting', 'A rules-made workstream whose one thread you archived holds no slot either',
      source='auto-sorting #3')
def _():
    names = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot']

    def setup(h, ctx):
        ids = rules_project(h, ctx)
        for name in names:
            h.workstream(ids, name)
        # Archiving the thread keeps its membership (restoring it brings it back), so the workstream stays listed and
        # empty: only FilingView.holds_slot keeps it out of the cap here.
        # A request that ran and finished, so nothing is pending and the thread can be archived.
        thread = h.thread(ids, '', automatic=True)
        h.grant(ids, thread=thread)
        h.scripted(ids, thread, 'Fix login: the session expires after an hour', result_text='Done: the session now lasts a day.')
        ctx['rules_made'] = h.organisation(ids, thread)['group']
        h.call('conversation_archive', projectId=ids.project, channelId=thread)

    def action(h, ctx):
        thread = typed(h, ctx, 'Add usage meters for Codex accounts')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        live = [i['name'] for i in h.ideas(ctx['ids'])['ideas']]
        if ctx['rules_made'] not in live:
            return False, f"setup: {ctx['rules_made']!r} isn't listed any more, so this checks nothing about the cap"
        taken = set(names) | {ctx['rules_made']}
        if not org['group'] or org['group'] in taken:
            return False, f"joined {org['group']!r} instead of getting its own (empty {ctx['rules_made']!r} counts toward the cap)"
        return True, f"new workstream {org['group']!r}; {ctx['rules_made']!r} still listed, empty"
    return setup, action, check


@case('s7-home-moves-rules-filed-thread', 'sorting', 'Home moves a rules-filed live-view thread into Preview pane, with Undo',
      provider='home', calls=1, source='auto-sorting #4')
def _():
    def setup(h, ctx):
        ids = home_project(h, ctx)
        h.workstream(ids, 'Preview pane')
        first = h.thread(ids, '', automatic=True, profile=ctx['fake'])
        h.message(ids, first, 'Capture a screenshot of the running app')
        h.file_by_hand(ids, first, 'Preview pane')
        # Shares no word with Preview pane or the thread you filed there, so the rules can't see where it goes and make
        # a workstream of their own ("Live view", checked in fake mode). "Tighten the screenshot typography" used to be
        # the request here, but since b11/sorting-t1 the rules file it under Preview pane themselves, which left Home
        # nothing to move.
        ctx['thread'] = h.thread(ids, '', automatic=True, profile=ctx['fake'])
        h.message(ids, ctx['thread'], 'Keep the live view of my app docked beside the chat')
        ctx['before'] = h.organisation(ids, ctx['thread'])['group']

    def action(h, ctx):
        return h.home(ctx['ids'], 'where do things stand?')

    def check(h, ctx, reply):
        if ctx['before'] == 'Preview pane':
            return False, 'setup: the rules already filed it under Preview pane, so this checks nothing about Home'
        moves = [c for c in reply.get('changes') or [] if c.get('kind') in ('filed', 'moved') and (c.get('thread') or {}).get('id') == ctx['thread']]
        after = h.organisation(ctx['ids'], ctx['thread'])['group']
        if after != 'Preview pane':
            return False, f"stays under {after!r} (rules put it in {ctx['before']!r}); Home changes: {[c.get('line') for c in reply.get('changes') or []]}"
        return bool(moves and moves[0].get('can_undo')), f"moved to Preview pane, undo={bool(moves and moves[0].get('can_undo'))}"
    return setup, action, check


@case('s8-home-leaves-hand-filed-threads', 'sorting', 'Three threads you filed by hand in Design stay there on arrival',
      provider='home', calls=1, source='auto-sorting #4')
def _():
    def setup(h, ctx):
        ids = home_project(h, ctx)
        h.workstream(ids, 'Design')
        ctx['threads'] = []
        for name, text in (('Release notes', 'Write the release notes for beta 11'), ('Installer check', 'Check the installer signs on a clean Mac'),
                           ('Changelog', 'Update the changelog for the beta release')):
            thread = seed_thread(h, ctx, name, text, 'Done: ' + text.lower() + '.', goal=text)
            h.file_by_hand(ids, thread, 'Design')
            ctx['threads'].append(thread)

    def action(h, ctx):
        return h.arrive(ctx['ids'])

    def check(h, ctx, reply):
        moved = [c for c in reply.get('changes') or [] if (c.get('thread') or {}).get('id') in ctx['threads']]
        groups = [h.organisation(ctx['ids'], t)['group'] for t in ctx['threads']]
        if moved or any(g != 'Design' for g in groups):
            return False, f'moved {len(moved)}; groups now {groups}'
        mentions_move = has_any(reply.get('text'), 'move ', 'moved ', 'refile', 'belong')
        return not mentions_move, f'all three still in Design; reply {"talks about moving" if mentions_move else "says nothing about moving"}'
    return setup, action, check


@case('s9-home-status-question-makes-no-workstream', 'sorting', '"how\'s the UI work going?" gets an answer and no new workstream',
      provider='home', calls=1, source='auto-sorting #4')
def _():
    def setup(h, ctx):
        ids = home_project(h, ctx)
        design = h.workstream(ids, 'Design')
        for name, text in (('Sidebar spacing', 'Tighten the sidebar spacing between groups'), ('Composer polish', 'Make the composer hint shorter')):
            thread = seed_thread(h, ctx, name, text, 'Done: ' + text.lower() + '.', goal=text)
            h.assign(ids, thread, design['id'])
        ctx['count'] = len(h.ideas(ids)['ideas'])

    def action(h, ctx):
        return h.home(ctx['ids'], "how's the UI work going?")

    def check(h, ctx, reply):
        ideas = h.ideas(ctx['ids'])['ideas']
        if len(ideas) != ctx['count']:
            return False, f"workstreams went from {ctx['count']} to {len(ideas)}: {[i['name'] for i in ideas]}"
        text = reply.get('text') or ''
        return has_any(text, 'sidebar', 'composer', 'design'), f"no new workstream; reply: {text[:80]!r}"
    return setup, action, check


@case('s10-home-keeps-workstreams-tidy', 'sorting', 'Nine typed threads: at most 7 workstreams, short noun names, no near-duplicates',
      provider='home', calls=1, source='auto-sorting #4, row 10')
def _():
    def setup(h, ctx):
        ids = home_project(h, ctx)
        for text in NINE:
            thread = h.thread(ids, '', automatic=True, profile=ctx['fake'])
            h.message(ids, thread, text)

    def action(h, ctx):
        return h.arrive(ctx['ids'])

    def check(h, ctx, reply):
        names = [i['name'] for i in h.ideas(ctx['ids'])['ideas']]
        problems = []
        if len(names) > 7:
            problems.append(f'{len(names)} workstreams')
        long = [n for n in names if not 1 <= len(n.split()) <= 3]
        if long:
            problems.append(f'long names {long}')
        folded = [n.casefold().rstrip('s') for n in names]
        if len(set(folded)) != len(folded):
            problems.append('near-duplicate names')
        return (not problems), (('; '.join(problems) + f' {names}') if problems else f'{len(names)} workstreams: {names}')
    return setup, action, check


@case('s12-not-filed-yet-stays', 'sorting', 'A thread you moved to Not filed yet stays unfiled through two arrivals',
      provider='home', calls=2, source='auto-sorting row 12')
def _():
    def setup(h, ctx):
        ids = home_project(h, ctx)
        h.workstream(ids, 'Releases')
        ctx['thread'] = seed_thread(h, ctx, 'Beta notes', 'Write the release notes for beta 11', 'Wrote the release notes for beta 11.', goal='Release notes for beta 11')
        state = h.organisation(ids, ctx['thread'])
        h.call('organisation_set', projectId=ids.project, channelId=ctx['thread'], expectedRevision=state['revision'],
               name=state['name'], group=None, nameLocked=state['nameLocked'], groupLocked=True)

    def action(h, ctx):
        first = h.arrive(ctx['ids'])
        second = h.arrive(ctx['ids'])
        return dict(first=first, second=second)

    def check(h, ctx, replies):
        group = h.organisation(ctx['ids'], ctx['thread'])['group']
        return group is None, f"group is {group!r} after two arrivals"
    return setup, action, check


def fake_answer_case(id, title, answer, *, asking, source, expect_fail=False):
    """A chat thread whose fake answer ends the given way; the activity index's ``ended`` must (not) read asking."""
    @case(id, 'sorting', title, source=source, expect_fail=expect_fail)
    def _():
        def setup(h, ctx):
            ids = rules_project(h, ctx)
            ctx['thread'] = h.thread(ids, 'chat')

        def action(h, ctx):
            task = h.scripted(ctx['ids'], ctx['thread'], 'Migrate the settings rows', result_text=answer)
            return dict(task_state=task['state'], activity=h.activity(ctx['ids'], ctx['thread']), pickup=h.pickup(ctx['ids'], ctx['thread']))

        def check(h, ctx, out):
            ended, question = out['activity'].get('ended'), out['pickup'].get('question')
            is_asking = ended == 'asking' or bool(question)
            if is_asking != asking:
                return False, f"ended={ended!r}, question={question}; wanted {'asking' if asking else 'not asking'}"
            return True, f"ended={ended!r}, question={question}"
        return setup, action, check
    return _


for _made in [
    fake_answer_case('s13-real-question-is-asking', 'An answer ending "Should I also migrate the old rows, or leave them?" needs you',
                     'Migrated the settings rows. Should I also migrate the old rows, or leave them?', asking=True,
                     source='auto-sorting #5'),
    fake_answer_case('s14-stock-offer-is-not-asking', '"Want me to add a test for this too?" is a stock offer, not a question',
                     'Migrated the settings rows and checked them. Want me to add a test for this too?', asking=False, source='auto-sorting #5'),
    fake_answer_case('s16-question-in-code-block-is-not-asking', 'A question inside a code block does not count',
                     'Migrated the rows. The query was:\n```sql\nSELECT * FROM rows WHERE old = ?\n```', asking=False, source='auto-sorting #5'),
]:
    pass


@case('s15-changed-files-mean-review-not-asking', 'sorting', 'A run that changed files and ends "Anything else?" is ready for review, not asking',
      source='auto-sorting #5')
def _s15():
    from evals.harness import write_tool_calls

    def setup(h, ctx):
        ids = rules_project(h, ctx)
        ctx['thread'] = h.thread(ids, 'edit')
        h.grant(ids)
        h.call('coding_acceptance_set', projectId=ids.project, channelId=ctx['thread'], required=True)

    def action(h, ctx):
        calls = write_tool_calls('greeting.py', 'def greet(name):\n    return f"Hello there, {name}"\n')
        task = h.scripted(ctx['ids'], ctx['thread'], 'Warm up the greeting', result_text='Changed greeting.py. Anything else?', tool_calls=calls)
        return dict(task_state=task['state'], pickup=h.pickup(ctx['ids'], ctx['thread']), activity=h.activity(ctx['ids'], ctx['thread']))

    def check(h, ctx, out):
        kind, question = out['pickup'].get('kind'), out['pickup'].get('question')
        if out['task_state'] != 'ready_for_review':
            return False, f"task ended {out['task_state']!r}, not ready_for_review"
        return (kind == 'review' and not question), f"kind={kind!r}, question={question}"
    return setup, action, check


@case('s17-second-review-still-needs-you', 'sorting', 'Approving task A leaves task B still needing you', source='auto-sorting row 17')
def _s17():
    from evals.harness import write_tool_calls

    def setup(h, ctx):
        ids = rules_project(h, ctx)
        ctx['a'], ctx['b'] = h.thread(ids, 'A'), h.thread(ids, 'B')
        h.grant(ids)
        h.call('coding_acceptance_set', projectId=ids.project, channelId=ctx['a'], required=True)
        # Both results keep the check green: a comment in each file, nothing the test reads. B first, then A, so A's
        # evidence is current when it's approved (a later write to a granted file stales the earlier result's review).
        ctx['task_b'] = h.scripted(ids, ctx['b'], 'Comment the test', result_text='Changed test_sample.py.',
                                   tool_calls=write_tool_calls('test_sample.py', '# B\nimport unittest\nfrom greeting import greet\n\n\nclass Sample(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(greet("a"), "Hello, a")\n'))
        ctx['task_a'] = h.scripted(ids, ctx['a'], 'Comment the greeting', result_text='Changed greeting.py.',
                                   tool_calls=write_tool_calls('greeting.py', 'def greet(name):\n    return f"Hello, {name}"  # A\n'))

    def action(h, ctx):
        h.review(ctx['ids'], ctx['a'], ctx['task_a'], 'approved')
        return dict(a=h.pickup(ctx['ids'], ctx['a']), b=h.pickup(ctx['ids'], ctx['b']))

    def check(h, ctx, out):
        return bool(out['b'].get('needs')) and not out['a'].get('needs'), f"A needs={out['a'].get('needs')}, B needs={out['b'].get('needs')} ({out['b'].get('kind')})"
    return setup, action, check


# ---------------------------------------------------------------------- hard cases, 20 Sep (notes/smartness/hard-cases-20260920.md)
# A rename the rules must respect, twenty threads in six clusters (rules alone, then Home on arrival), a real question
# that isn't the closing sentence, a blocking question on a run that changed files, and a follow-up whose topic is not
# the thread you had open.

HARD = 'hard-cases 20 Sep'

CLUSTERS = {
    'login': ['Fix login: the session expires after an hour', 'Login button is misaligned on the sign-in screen',
              'The remember me checkbox on login does nothing', 'Login page flashes white before it loads'],
    'export': ['Crash when exporting invoices to CSV', 'Export to PDF hangs forever', 'Add an export button to the invoices list'],
    'sidebar': ['Make the sidebar 20% narrower', 'Tighten the sidebar spacing between groups', 'Sidebar jumps when a run finishes',
                'Add a collapse toggle to the sidebar'],
    'release': ['Write the release notes for beta 11', 'Bump the version for the beta 11 release', 'Check the installer signs on a clean Mac before the release'],
    'home': ['Home keeps filing threads under the wrong workstream', 'The Home summary repeats itself', 'Home should say what needs me first'],
    'tests': ['Fix the flaky date test', 'Speed up the test suite, it takes four minutes', 'Add a test for the greeting module'],
}


def cluster_report(h, ctx, threads):
    """threads: {cluster: [thread ids]}. Problems with the filing, or [] when every cluster sits in one workstream and
    the project has at most seven."""
    groups = {cluster: [h.organisation(ctx['ids'], t)['group'] for t in ids] for cluster, ids in threads.items()}
    problems = []
    unfiled = [c for c, gs in groups.items() if any(g is None for g in gs)]
    if unfiled:
        problems.append(f'unfiled threads in {unfiled}')
    split = {c: sorted(set(g for g in gs if g)) for c, gs in groups.items() if len(set(gs)) > 1}
    if split:
        problems.append(f'clusters split across workstreams: {split}')
    distinct = sorted(set(g for gs in groups.values() for g in gs if g))
    if len(distinct) > 7:
        problems.append(f'{len(distinct)} workstreams')
    long = [n for n in distinct if not 1 <= len(n.split()) <= 3]
    if long:
        problems.append(f'long names {long}')
    return problems, groups, distinct


@case('s40-renamed-workstream-is-not-remade', 'sorting', 'You renamed the rules\' Authentication workstream to Sign-in; the next login thread joins Sign-in, no new Authentication',
      source=HARD + ', your rename vs the topic dictionary')
def _():
    def setup(h, ctx):
        ids = rules_project(h, ctx)
        first = typed(h, ctx, 'Fix login: the session expires after an hour')
        ctx['made'] = h.organisation(ids, first)['group']
        idea = next(i for i in h.ideas(ids)['ideas'] if i['name'] == ctx['made'])
        h.call('ideas_save', projectId=ids.project, profileId=ids.profile, ideaId=idea['id'], expectedRevision=idea['revision'],
               name='Sign-in', summary=idea.get('summary') or '', state=idea['state'], target=idea.get('target'))

    def action(h, ctx):
        thread = typed(h, ctx, 'Fix the login timeout on the settings page')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        names = [i['name'] for i in h.ideas(ctx['ids'])['ideas']]
        if org['group'] != 'Sign-in':
            return False, f"filed under {org['group']!r}, not Sign-in (was {ctx['made']!r}); workstreams now {names}"
        return True, f'joined Sign-in; workstreams {names}'
    return setup, action, check


@case('s41-twenty-threads-six-clusters-by-rules', 'sorting', 'Twenty typed threads in six plain clusters: every cluster in one workstream, at most seven in all',
      source=HARD + ', scale')  # fake mode 20 Sep: the export threads split into Export and Invoices; 21 Sep: filing folds word forms (project_ideas.filing_terms)
def _():
    def setup(h, ctx):
        rules_project(h, ctx)

    def action(h, ctx):
        threads = {}
        for cluster, texts in CLUSTERS.items():
            threads[cluster] = [typed(h, ctx, text) for text in texts]
        return threads

    def check(h, ctx, threads):
        problems, groups, distinct = cluster_report(h, ctx, threads)
        return (not problems), ('; '.join(problems) + f'; groups {groups}') if problems else f'{len(distinct)} workstreams: {distinct}'
    return setup, action, check


@case('s42-twenty-threads-after-home-arrives', 'sorting', 'The same twenty threads, then Home arrives: clusters stay together, still at most seven workstreams',
      provider='home', calls=1, source=HARD + ', scale')
def _():
    def setup(h, ctx):
        ids = home_project(h, ctx)
        ctx['threads'] = {}
        for cluster, texts in CLUSTERS.items():
            made = []
            for text in texts:
                thread = h.thread(ids, '', automatic=True, profile=ctx['fake'])
                h.message(ids, thread, text)
                made.append(thread)
            ctx['threads'][cluster] = made
        ctx['before'] = cluster_report(h, ctx, ctx['threads'])[0]

    def action(h, ctx):
        return h.arrive(ctx['ids'])

    def check(h, ctx, reply):
        problems, groups, distinct = cluster_report(h, ctx, ctx['threads'])
        moves = len([c for c in reply.get('changes') or [] if c.get('kind') in ('filed', 'moved')])
        if problems:
            return False, '; '.join(problems) + f'; Home moved {moves}; rules alone had {ctx["before"] or "no problems"}; groups {groups}'
        return True, f'{len(distinct)} workstreams after arrival ({moves} moves by Home): {distinct}'
    return setup, action, check


fake_answer_case('s43-question-before-the-summary-still-asks', 'A real question followed by a summary sentence still needs you',
                 'Should I migrate the old rows too, or leave them? For now I migrated only the new rows and left the old table alone.', asking=True,
                 source=HARD + ', question not at the tail')  # fake mode 20 Sep: only the closing sentence was read; 21 Sep: a "for now I ..." tail keeps the question open

fake_answer_case('s44-question-answered-by-the-agent-is-not-asking', 'An agent that asks and then answers itself ("Keep them? I kept them.") does not need you',
                 'Migrated the settings rows. Keep the old rows? I kept them, since nothing reads the new table yet.', asking=False,
                 source=HARD + ', rhetorical question')


@case('s45-blocking-question-on-a-changed-file', 'sorting', 'A run that changed greeting.py, broke the check and ends asking Hi or Hello surfaces the question, not just review',
      source=HARD + ', two signals disagree')  # fake mode 20 Sep: review won and the question was dropped; 21 Sep: the review keeps its kind, the question is its Next
def _s45():
    from evals.harness import write_tool_calls

    def setup(h, ctx):
        ids = rules_project(h, ctx)
        ctx['thread'] = h.thread(ids, 'edit')
        h.grant(ids)
        h.call('coding_acceptance_set', projectId=ids.project, channelId=ctx['thread'], required=True)

    def action(h, ctx):
        calls = write_tool_calls('greeting.py', 'def greet(name):\n    return "Hi"\n')
        task = h.scripted(ctx['ids'], ctx['thread'], 'Shorten the greeting',
                          result_text='Changed greeting.py to return Hi, but test_sample expects Hello with the name. Should the greeting be Hi or Hello?', tool_calls=calls)
        return dict(task_state=task['state'], pickup=h.pickup(ctx['ids'], ctx['thread']), activity=h.activity(ctx['ids'], ctx['thread']))

    def check(h, ctx, out):
        pickup = out['pickup']
        asked = bool(pickup.get('question')) and has_any(pickup.get('next'), 'Hi', 'Hello')
        return asked, f"task {out['task_state']!r}; pickup kind={pickup.get('kind')!r}, question={pickup.get('question')}, next={pickup.get('next')!r}; ended={out['activity'].get('ended')!r}"
    return setup, action, check


@case('s46-also-with-its-own-topic-skips-the-open-thread', 'sorting', '"also make the meters refresh live" joins the usage-meter thread, not the picker thread you had open',
      source=HARD + ', recency vs topic')
def _():
    def setup(h, ctx):
        ids = rules_project(h, ctx)
        ctx['picker'] = typed(h, ctx, PICKER)
        ctx['meters'] = typed(h, ctx, 'Add usage meters for Codex accounts')
        h.call('conversation_seen', projectId=ids.project, channelId=ctx['picker'])

    def action(h, ctx):
        thread = typed(h, ctx, 'also make the meters refresh live, not only when the sidebar opens')
        return h.organisation(ctx['ids'], thread)

    def check(h, ctx, org):
        picker = h.organisation(ctx['ids'], ctx['picker'])['group']
        meters = h.organisation(ctx['ids'], ctx['meters'])['group']
        if org['group'] != meters:
            return False, f"filed under {org['group']!r}; meters thread is in {meters!r}, picker (open) in {picker!r}"
        return True, f'joined {meters!r} (picker was open, in {picker!r})'
    return setup, action, check


CASES = REGISTRY['sorting']
