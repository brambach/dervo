"""Offline recall check for project memory (notes/MEMORY-explore-20261005.md, "Building A", step 1). No model, no live
database: an in-memory store holds 57 notes in this project's own style, and labelled requests go through
``project_memory.context_block``, the path a real message takes. A change to how notes are picked is measured here
before any eval spends a model call.

The notes are the explore's ``notes.json`` (evidence/design-tests/memory-explore-20261005 in the mvp1 folder): 30 sent
to a real run on 5 Oct, with a tester's address taken out, plus 27 synthetic ones marked as such, and three of this
project's agent-made rules as notes moved from rules (step 8) would read. Which notes a request
needs is a judgement, written into CASES, so treat the counts as examples, not rates. On beta.17 (203a8fe) "polish the
terminal" and "build 0.1.0-beta.17.1" miss notes they need: nothing matched by words, so the 30 newest went.

    python3.13 -B release_checks.py test_memory_recall
    python3.13 -B test_memory_recall.py   # the table, for a before and after
"""
import json
import re
import time
import unittest

import project_memory
from test_project_memory import Coding, task

NOTES = [
    'Bryce\'s NEXT-UP.md #41 (3 Oct 2026) only says "make memory better and faster"; the 5 Oct explore thread for it writes notes/MEMORY-explore-20261005.md and mocks under evidence/design-tests/memory-explore-20261005/ in /Users/bryce/dervo/app.',  # 0
    'Dervo checks changes here with its Node test check (node --no-addons --no-experimental-fetch titlebar.test.mjs); it last passed on 4 Oct.',  # 1
    'Backend host tests (test_task_lessons, test_conversation_memory, test_knowledge_delivery) stop at "Dervo beta access needs confirmation" in a source checkout unless account_config.create is stubbed, as headless_driver.serve does; with it stubbed, 7 test_task_lessons.CodexLessons tests still fail on d1111ea.',  # 2
    'In notes saved from the mvp1 folder, write paths in other worktrees as absolute paths (/Users/bryce/dervo/app-wt/...). Dervo 16.4 judges a relative path against the project folder only, so a worktree path reads as missing and the note retires as out of date.',  # 3
    "The browser preview build (evidence/preview-harness/build.sh) needs the repo's root scripts/ folder as well as frontend/, since vite loads scripts/model-capabilities.json; a git archive of frontend alone fails with UNLOADABLE_DEPENDENCY.",  # 4
    "When a run from the mvp1 folder edits code in an app-wt worktree, Dervo won't commit it, so hand Bryce a commit script that checks the exact file list and hashes before any next step (like a merge) that needs a clean worktree.",  # 5
    "A rule added to a beta plan after a build thread has started doesn't reach that thread. It keeps working from its original request. When a new thread takes files a running one may touch, ask Bryce to pass the rule on straight away, or plan for the merge conflict.",  # 6
    "Bruno, the tester who asked for Dervo in Portuguese (PLAN-beta17-20260928.md section 2), is <his email>; Bryce writes to testers from his own address, and on 2 Oct 2026 he emailed Bruno that pt-BR is coming in beta.17 and he'd post in Discord when it's out.",  # 7
    'Baseline on d1111ea (beta16.4) inside a Dervo run, 2 Oct 2026: 13 of 3,063 backend tests fail (release_checks.py per module, framework Python 3.13) and 205 of about 7,700 frontend tests fail. Logs are in /tmp/b17-trial/out/base and fe-base, and the trial merge results are in PLAN-beta17-20260928.md.',  # 8
    '0.1.0-beta.17 was built on 2 Oct 2026 from /Users/bryce/dervo/app-wt/b17 at 203a8fe (20 managed steps, notarised); the app is in ~/Dervo Releases/0.1.0-beta.17 and its records are in /Users/bryce/dervo/feedback/beta17-20261002/candidate.json.',  # 9
    "The preview harness's older ?fr= first-run states (evidence/preview-harness/fixtures-zfirstrun.js) stop on beta.17's Welcome screen; fixtures-zzfirstrun-codex.js steps past it by class (.dv-wm-go), so it works in English and in headless Chromium --lang=pt-BR.",  # 10
    'When the host gets a better message for one screen (like connections_ui.mismatch_detail in 16.2), search every screen that shows the same state. First run kept a fixed "different account" line for identity_mismatch while Connected accounts showed the real reason, so plan problems read as account problems.',  # 11
    "The GitHub Windows workflow's two downloads are named with the full commit hash, dervo-windows-unsigned-<sha>.zip and windows-build-notes-<sha>.zip, and Bryce saves them to ~/Downloads. Matching the hash against git log on b16/windows (/Users/bryce/dervo/app-wt/b16-windows-m2) tells you which beta a Windows build includes.",  # 12
    "Bryce's 3 Oct 2026 Dervo showcase recording was re-exported (to drop the big cursor) as /Users/bryce/dd/videos/Dervo 2026-10-03_09-51-51-1918x1080-20261003100651.mp4, outside the project; the earlier 1920x1080 export of the same take is ...-1920x1080-20261003095614.mp4.",  # 13
    "Homebrew ffmpeg on Bryce's Mac has no drawtext or subtitles filter, and Pillow can't open the brand fonts (frontend/src/local/brand-assets/*.woff2). evidence/x-posts/20261003-showcase/captions.mjs works round both by setting caption type in headless Chromium via the frontend's Playwright.",  # 14
    "Tomohiro Iida answered Bryce's paid-side question on Discord at 8:13 PM: he doesn't expect his team to become a paying customer because his Controller / Agent OS already covers execution and blocker tracking; he's still interested technically.",  # 15
    "On 3 Oct 2026 Adam Barta (@AdamBartas on X, an indie designer who runs the design studio Launch Studio per web search) sent Bryce an X message request praising Dervo's logo and asking if he's working on it solo; it reads like a design-studio lead-in, not a tester signup.",  # 16
    'Bryce\'s own reports say "Build: 0.1.0" because he runs /Applications/Dervo Local.app, whose Info.plist version is 0.1.0, so a report\'s build line doesn\'t tell you which beta\'s code he was on.',  # 17
    "notes/MUSE-research-20261003.md (#43): Muse, Instinct, consumer Grok and xAI's Grok Bot all have no API for an outside app to see or message them. Grok (custom MCP) and Muse (reviewed connector) can call a Dervo MCP server instead.",  # 18
    "A read-only Dervo chat on the mvp1 folder (/Users/bryce/dervo/app) can only read relative paths inside it and refuses absolute ones, so it can't open beta code in /Users/bryce/dervo/app-wt/* to check a note's file:line citations; that needs a chat set up on the worktree.",  # 19
    "When another thread runs in the same project folder, Dervo credits a full-access run only with files its own Edit/Write tools touched. Renders, heredoc files and anything else a script writes stay untracked and aren't committed.",  # 20
    "In beta.17, account linking (Settings, Connected accounts, Help between your accounts) only offers personal accounts and has no unlink (/Users/bryce/dervo/app-wt/b17/frontend/src/local/ConnectedAccounts.tsx:361-370), so a work project can't get a cross-tool reviewer today.",  # 21
    'On 5 Oct 2026 Bryce lifted his 3 Oct hold for three beta.17.1 build threads: #46 (latest models, GPT-5.5 leaves Codex 14 Oct), the #36-38 import bugs together, and the three review bugs in notes/REVIEW-explore-20261003.md; the #27 and #42 redesigns stay unbuilt.',  # 22
    'Beta.17 folds the sidebar for a second column only under a 1240 px window (PreviewPane.tsx:34), so with Preview or a 460 px beside column open, 1240 to about 1340 windows leave a 422 to 512 px composer whose tools wrap on a long model chip.',  # 23
    "In beta.17, CollaborationSettings in /Users/bryce/dervo/app-wt/b17/frontend/src/local/CollaborationUI.tsx (line 182 on) is the old agents form and isn't mounted, so its warnings aren't what users see; the live review hints are in ModelPicker.tsx's This message section.",  # 24
    "Bryce's 5 Oct 2026 design picks, none built yet: right bar B, sidebar folds when something beside leaves the chat under 600 px, watching A, subthreads as live rows in the parent plus Open beside (notes/WORKSPACE-explore-20261003.md); review on the answer with Read the exchange beside.",  # 25
    "In beta.17 a run started on the user's behalf (passed note, answered question, fanout) carries 'clicked': True only when the source run had full access (backend/thread_notes.py request_for, thread_fanout.py:205), meaning the user clicked.",  # 26
    'GPT-5.5 leaves ChatGPT and Codex on 14 Oct 2026 but stays on the OpenAI API; the 19 Sep plan (evidence/native-20260919/models-and-pickers.md:105 and :229, PLAN-native.md:448) is to show the retirement in place with a one-click move to the named successor.',  # 27
    'On 5 Oct 2026 Bryce put automatic note passing in beta.17.1 (b171/notes): a per-project setting, on by default, with waits-for-you notes, no ping-pong, a visible Passed on automatically card, and same access as the sender.',  # 28
    "The app-wt worktrees' frontend/node_modules now links to /Users/bryce/dervo/app/frontend/node_modules (as /Users/bryce/dervo/app-wt/b17 does; app-wt/project-terminal-20260922 no longer exists). New b171-* worktrees start without the link, and Playwright there is @playwright/test.",  # 29
    'Beta.17.1 work is split across five worktrees branched from beta17 (203a8fe): /Users/bryce/dervo/app-wt/b171-notes, b171-models, b171-import, b171-review and b171-finish.',  # 30
    '[synthetic] The managed release builder refuses to start under 40 GB free; check df -h / before a native build and inventory ~/Dervo Releases first.',  # 31
    '[synthetic] The project terminal (frontend/src/local/ProjectTerminal.tsx) uses xterm.js; its addons come from the shared frontend install and the fit addon resizes on the Preview column toggle.',  # 32
    '[synthetic] Discord announcements for testers go in #beta-news; Bryce posts them himself after a release is notarised.',  # 33
    '[synthetic] backend/history_import.py names imported conversations with the naming rules in backend/naming.py and abstains on very short first messages.',  # 34
    "[synthetic] Codex runs read AGENTS.md and Claude runs read CLAUDE.md; repo_guidance.py sends both as text so each provider sees the other's file.",  # 35
    '[synthetic] The pt-BR catalog lives in frontend/src/i18n/pt-BR.ts and every key must exist in en.ts; the catalog test fails on a missing key.',  # 36
    '[synthetic] Home routes a dictated request to a thread with backend/project_orchestrator.py; Codex accounts got Home in beta.17.',  # 37
    '[synthetic] The waitlist lives in Supabase; backend/supabase_account.py reads invites, and the service key is only on the release Mac.',  # 38
    '[synthetic] Screenshots for design reviews are taken at 1512 and 854 wide in light and dark, then sized to 1600 px or less with sips.',  # 39
    "[synthetic] The Knowledge page's Retired to make room list shows the newest 20 retired notes with Bring back.",  # 40
    '[synthetic] Run limits for 17.1 are per project in Settings, project, Access: idle 2 h, cap 24 h, 64 attempts.',  # 41
    '[synthetic] notes/REVIEW-explore-20261003.md proposes review on the answer with Read the exchange beside, and the other tool as default reviewer.',  # 42
    '[synthetic] The Windows build is unsigned for the beta; testers allow it in SmartScreen with More info, then Run anyway.',  # 43
    "[synthetic] Project icons (#45) come from the folder's favicon or app icon when one exists, otherwise a letter tile.",  # 44
    "[synthetic] Bryce prefers one thread per worktree; two threads editing one worktree leave each other's files uncommitted.",  # 45
    "[synthetic] Backend tests run with python3 -m unittest from backend/, on the framework Python 3.13, not Homebrew's.",  # 46
    "[synthetic] Catch up's Go through these screen (ProjectCatchup.tsx) moves with Up and Down or j and k since beta.17.",  # 47
    '[synthetic] The landing page source is in website-wt/rv-web; trydervo.com deploys from its main branch through Cloudflare Pages.',  # 48
    "[synthetic] dervo_search_history reads chats' requests, answers and goals, plus kept and retired notes since beta.17.",  # 49
    '[synthetic] Bryce reports bugs with Command Shift B, which files them into NEXT-UP.md with the screen and thread attached.',  # 50
    '[synthetic] The model list check for each beta is frontend/scripts/check-models.mjs on b171/models.',  # 51
    "[synthetic] A thread's thinking level beats the project's since 16.4; a new thread runs at the project's level.",  # 52
    "[synthetic] Reviewer runs get their packet only, never project memory, so a reviewer doesn't see project notes.",  # 53
    '[synthetic] The memory explore and the terminal explore both write only under notes/ and evidence/design-tests/.',  # 54
    '[synthetic] Bryce writes in Australian English: colour, organise, notarise.',  # 55
    '[synthetic] The Mac app ships its own Python 3.13 since beta.17, from python-build-standalone.',  # 56
    # Rules moved to notes (#41, step 8), in shortened words as Move's Edit would save them. From 57 on, notes are the
    # person's own (edited_by), as a moved rule is.
    'When recording frames of a CSS-scaled page with Playwright screenshots, wait for two requestAnimationFrame callbacks after each transform change, or single frames can come out with transparent holes.',  # 57
    'When comparing test failures between a branch and a baseline in scratch copies, archive frontend/, src/ and backend/ together: many frontend tests read src/bridge.rs or backend/, and a frontend-only copy fails them with ENOENT.',  # 58
    'When proving a new test fails without its fix, take the fix out and put it back inside one shell command with a trap on EXIT, so a closed Dervo never leaves the branch without the fix.',  # 59
]
MOVED_FROM = 57

# (request, what the chat was already doing or '', the notes it needs by index). A goal is the chat's earlier run's
# reported goal, for a follow-up that says little itself ("ok go"). People notes are labelled like any other note:
# whether they go to coding runs at all is Bryce's open question (question 4 in the explore), not this test's.
CASES = [
    ('polish the terminal', '', {32}),
    ('build 0.1.0-beta.17.1', '', {9, 31}),
    ('run the frontend tests on b171-import', '', {29}),
    ('make memory better and faster', '', {0}),
    ('email Bruno that pt-BR is out', '', {7}),
    ("the Windows build won't open on the tester's PC", '', {12, 43}),
    ('redo the captions on the showcase video', '', {13, 14}),
    ('reply to Tomohiro on Discord', '', {15}),
    ('add the pt-BR string for the new Catch up button', '', {36}),
    ('why does my bug report say Build 0.1.0', '', {17}),
    ('move threads off GPT-5.5 before it retires', '', {27}),
    ('cut the 17.1 release once every branch is merged', '', {30, 31}),
    ('baseline the backend tests before the merge', '', {2, 8, 46}),
    ('take screenshots of the sidebar for the design review', '', {39}),
    ('add a preview harness fixture for the first run', '', {4, 10}),
    ('ok go', 'Fix how imported conversations are named when the first message is very short', {34}),
    ('change the hero on the landing page', '', {48}),
    ("a review thread's files weren't committed in the mvp1 folder", '', {20, 5}),
    ('can a work project get a Codex reviewer', '', {21}),
    ('Home sends my dictated requests to the wrong thread', '', {37}),
    ('where do the run limits live in Settings', '', {41}),
    ('record the launch video frames with Playwright', '', {57}),
    ('compare the failing tests with a baseline copy', '', {58}),
    ('prove the new test fails without the fix', '', {59}),
]
NOW = int(time.time())
SENT_LINE = re.compile(r'^- \[(m\d\d)\] ', re.M)


def store(coding):
    project_memory.schema(coding.store.db)
    with coding.store.db:
        for n, text in enumerate(NOTES):
            # Index 0 is the newest, as in measure.py: two hours between notes.
            item = dict(id=f'm{n:02d}', text=text, created_at=NOW - n * 7200, updated_at=NOW - n * 7200, provider='claude',
                        source=dict(thread_id='elsewhere', task_id='old', run_id='r', visibility='shared'))
            if n >= MOVED_FROM:
                item.update(provider='person', edited_by='owner', edited_at=item['created_at'],
                            source=dict(kind='rule', rule_id=f'rule{n}', version_id='v1', batch='b', visibility='shared'))
            coding.store.db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', (item['id'], 'p', 'personal', 'active', json.dumps(item)))


def deliver(coding, n, request, goal=''):
    """The memory block a request gets, in a chat of its own. With a goal, the message continues a session whose earlier
    run reported that goal and was sent no notes, so the first block is the whole one either way."""
    thread = f'chat{n}'
    if goal:
        earlier = task(coding, f'earlier{n}', thread=thread, state='answered', text=goal, at=NOW - 600)
        earlier['runs'][0].update(state='completed', guide=dict(goal=goal))
        coding.save(earlier)
        current = task(coding, f'ask{n}', thread=thread, text=request, continuity=dict(state='continues', from_task_id=f'earlier{n}'))
    else:
        current = task(coding, f'ask{n}', thread=thread, text=request)
    return project_memory.context_block(coding, current)


def table():
    coding = Coding()
    store(coding)
    rows = []
    for n, (request, goal, needed) in enumerate(CASES):
        block = deliver(coding, n, request, goal)
        sent = {int(id[1:]) for id in SENT_LINE.findall(block)}
        notes_chars = sum(len(NOTES[i]) for i in sent)
        rows.append(dict(request=request, needed=sorted(needed), sent=len(sent), missed=sorted(needed - sent),
                         notes_chars=notes_chars, block_chars=len(block)))
    return rows


class RecallTests(unittest.TestCase):
    def test_every_labelled_request_is_sent_the_notes_it_needs(self):
        missed = [(row['request'], row['missed']) for row in table() if row['missed']]
        self.assertEqual(missed, [])

    def test_what_one_request_is_sent_stays_small(self):
        rows = table()
        self.assertEqual(len(rows), len(CASES))
        for row in rows:
            # About 4 KB of notes in full, down from 7.7 KB on beta.17, and the whole block with the index of the other
            # 45 under 7.5 KB, down from 8.4 to 8.9 KB (measured on 203a8fe, 5 Oct).
            self.assertLessEqual(row['notes_chars'], 4500, row['request'])
            self.assertLessEqual(row['block_chars'], 7500, row['request'])

    def test_a_request_matching_nothing_still_hears_about_every_note(self):
        """Negative control: fewer notes in full never means a note goes unmentioned. Every active note is either sent
        or named in the index line, so the agent knows to search for it."""
        coding = Coding()
        store(coding)
        block = deliver(coding, 99, 'zzz qqq')
        sent = set(SENT_LINE.findall(block))
        self.assertTrue(sent, 'the newest notes still go when nothing matches')
        named = set(re.findall(r'\[(m\d\d)\]', block))
        self.assertEqual(named, {f'm{n:02d}' for n in range(len(NOTES))})


if __name__ == '__main__':
    print(json.dumps(table(), indent=1))
