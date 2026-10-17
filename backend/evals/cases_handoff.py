"""Handoff and catch-up. From notes/smartness/handoff-catchup.md, section 2.

T cases read ``runs[-1].told.transcript`` or the pickup, the exact text and rows Dervo hands over, with no model.
R cases seed the history through the fake, then move the thread to a real account (conversation_profile_set, as the
account picker does) and send one probe; the check is on the answer or on the resulting diff.
"""
import subprocess
import time

from evals.case import REGISTRY, case
from evals.harness import FAKE_MODEL, answer_text, has, has_any, told, tools_used, write_tool_calls

GREET_OK = 'def greet(name):\n    return f"Hello, {name}"  # greeting\n'
GREET_BROKEN = 'def greet(name):\n    return "Hi"\n'
DATES = ('from datetime import datetime\n\n\ndef parse_date(text):\n    """Parse an ISO date. Drops the timezone: the offset is thrown away."""\n'
         '    return datetime.fromisoformat(text).replace(tzinfo=None)\n')
CLI = ('def parse_args(argv):\n    command = argv[0] if argv else "help"\n    if command == "greet":\n        return ("greet", argv[1:])\n'
       '    elif command == "farewell":\n        return ("farewell", argv[1:])\n    elif command == "help":\n        return ("help", [])\n'
       '    else:\n        raise SystemExit(f"unknown command {command}")\n')
# The same CLI with the bug e43's scripted finding names: parse_args([]) raises IndexError instead of returning help.
# The guarded CLI above made that finding false against the file, and both saved runs of 20 Sep read cli.py, said so,
# and fixed the one real bug: "fix it" was not ambiguous as seeded.
CLI_BARE_ARGV = CLI.replace('command = argv[0] if argv else "help"', 'command = argv[0]')


def seeded(h, ctx, files=None, grant=('greeting.py', 'test_sample.py'), acceptance=True):
    """A project on the fake profile with one thread, where the history is scripted."""
    ids = h.project(h.fake_profile(), FAKE_MODEL, files=files)
    ctx['ids'], ctx['thread'] = ids, h.thread(ids, 'chat')
    h.grant(ids, files=list(grant))
    if acceptance:
        h.call('coding_acceptance_set', projectId=ids.project, channelId=ctx['thread'], required=True)
    return ids


def probe_on(h, ctx, provider='codex'):
    """Move the seeded thread to the real agent (in fake mode, it stays on the fake)."""
    profile, model = h.agent(provider)
    if profile != ctx['ids'].profile:
        h.rebind(ctx['ids'], ctx['thread'], profile, model)


def transcript(task):
    return told(task).get('transcript') or ''


def tests_pass(path):
    result = subprocess.run(['python3', '-m', 'unittest', '-q', 'test_sample'], cwd=path, capture_output=True, text=True, timeout=60)
    return result.returncode == 0


# ---------------------------------------------------------------------- T: what the next agent is told
@case('e2-undone-result-is-told', 'handoff', 'After approve and Undo, the next message is told the change was undone, not "(idle)"',
      source='handoff #1, probed')
def _():
    def setup(h, ctx):
        seeded(h, ctx)
        task = h.scripted(ctx['ids'], ctx['thread'], 'Make greet return Hello with the name', result_text='Changed greeting.py.',
                          tool_calls=write_tool_calls('greeting.py', GREET_OK))
        h.review(ctx['ids'], ctx['thread'], task, 'approved')
        try:
            h.commit(ctx['ids'], ctx['thread'], task)
        except Exception:
            pass  # approval already committed
        h.undo(ctx['ids'], ctx['thread'], task)
        ctx['first'] = h.task_of(ctx['ids'], ctx['thread'], task['id'])

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Now add a farewell function next to it', result_text='Added farewell.')

    def check(h, ctx, task):
        text = transcript(task)
        ok = has_any(text, 'undid', 'undone', 'reverted') and '(idle)' not in text
        return ok, f"undo word: {has_any(text, 'undid', 'undone', 'reverted')}; '(idle)' present: {'(idle)' in text}; commit reverted: {bool((ctx['first'].get('commit') or {}).get('reverted'))}"
    return setup, action, check


@case('e3-rejected-result-is-told', 'handoff', 'A result you rejected reads as rejected to the next message, not as waiting for review',
      source='handoff #1')
def _():
    def setup(h, ctx):
        seeded(h, ctx)
        task = h.scripted(ctx['ids'], ctx['thread'], 'Make greet return Hello with the name', result_text='Changed greeting.py.',
                          tool_calls=write_tool_calls('greeting.py', GREET_OK))
        h.review(ctx['ids'], ctx['thread'], task, 'rejected')

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Try again, keep the old signature', result_text='Tried again.')

    def check(h, ctx, task):
        text = transcript(task)
        return has(text, 'rejected') and '(ready_for_review)' not in text, f"'rejected' present: {has(text, 'rejected')}; '(ready_for_review)' present: {'(ready_for_review)' in text}"
    return setup, action, check


@case('e6-goal-from-an-earlier-run-survives', 'handoff', 'A correction message without a guide still hands the goal from the run before it',
      source='handoff #3')
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Let greet take a title', result_text='greet takes a title now.',
                   guide=dict(goal='greet takes a title', step='Added the title parameter', next='Update the test'))
        h.scripted(ctx['ids'], ctx['thread'], 'Make the title optional', result_text='Made it optional.')

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Summarise where we are', result_text='Summary.')

    def check(h, ctx, task):
        text = transcript(task)
        line = has(text, 'Its goal: greet takes a title')
        return line, f"Dervo's goal line in transcript: {line}; phrase anywhere: {has(text, 'greet takes a title')}"
    return setup, action, check


@case('e15-stale-next-yields-to-dervo', 'handoff', 'A guide "next" written before the check passed is not the thread\'s Next once it\'s ready for review',
      source='handoff #5, probed')
def _():
    def setup(h, ctx):
        seeded(h, ctx)

    def action(h, ctx):
        h.scripted(ctx['ids'], ctx['thread'], 'Warm up the greeting', result_text='Changed greeting.py and the check passed.',
                   guide=dict(goal='Warm up the greeting', step='Editing greeting.py', next='Run the tests to confirm the new greeting.'),
                   tool_calls=write_tool_calls('greeting.py', GREET_OK))
        return h.pickup(ctx['ids'], ctx['thread'])

    def check(h, ctx, pickup):
        nxt = pickup.get('next') or ''
        return nxt == 'Your review, then commit.', f"kind={pickup.get('kind')!r}, next={nxt!r}"
    return setup, action, check


@case('e16-final-question-is-the-next', 'handoff', 'A final guide "next" that asks a question is the Next, flagged as a question', source='handoff #5, probed')
def _():
    def setup(h, ctx):
        seeded(h, ctx)

    def action(h, ctx):
        h.scripted(ctx['ids'], ctx['thread'], 'Warm up the greeting', result_text='Changed greeting.py.',
                   guide=dict(goal='Warm up the greeting', step='Done', next='Should I also update the README?'), guide_last=True,
                   tool_calls=write_tool_calls('greeting.py', GREET_OK))
        return h.pickup(ctx['ids'], ctx['thread'])

    def check(h, ctx, pickup):
        return has(pickup.get('next'), 'README') and bool(pickup.get('question')), f"next={pickup.get('next')!r}, question={pickup.get('question')}"
    return setup, action, check


@case('e17-answered-question-stops-showing', 'handoff', 'After you answer "No README", the old question is no longer the Next', source='handoff #5, probed')
def _():
    def setup(h, ctx):
        seeded(h, ctx)
        h.scripted(ctx['ids'], ctx['thread'], 'Warm up the greeting', result_text='Changed greeting.py.',
                   guide=dict(goal='Warm up the greeting', step='Done', next='Should I also update the README?'), guide_last=True,
                   tool_calls=write_tool_calls('greeting.py', GREET_OK))

    def action(h, ctx):
        h.scripted(ctx['ids'], ctx['thread'], 'No README', result_text='Understood, leaving the README alone.')
        return h.pickup(ctx['ids'], ctx['thread'])

    def check(h, ctx, pickup):
        return not has(pickup.get('next'), 'README'), f"next={pickup.get('next')!r}"
    return setup, action, check


@case('e19-lost-run-is-told-as-unconfirmed', 'handoff', 'After Dervo dies mid-run, the next message is told the run ended unexpectedly, not that it continues',
      source='handoff row 19 (regression H3)')
def _():
    def setup(h, ctx):
        seeded(h, ctx)
        h.scenario(h.fake_profile(), result_text='Still writing.', mode='hang', hang_seconds=240,
                   tool_calls=write_tool_calls('greeting.py', GREET_OK))
        scoped = h.scoped(ctx['ids'], ctx['thread'])
        task = h.request(scoped, 'Warm up the greeting')
        h.wait_until(lambda: len(tools_used(h.task(scoped, task))) >= 2 and h.task(scoped, task), timeout=90, what='two writes')
        h.kill()
        h.start()

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Carry on', result_text='Carrying on.')

    def check(h, ctx, task):
        text = transcript(task)
        ok = has_any(text, 'closed', 'confirmed', 'unexpected', 'lost') and not has(text, 'continues your session')
        return ok, f"says closed/unconfirmed: {has_any(text, 'closed', 'confirmed', 'unexpected', 'lost')}; continues: {has(text, 'continues your session')}"
    return setup, action, check


# ---------------------------------------------------------------------- R: what the next agent does with it
def unrelated(h, ctx, count, start=1):
    for n in range(start, start + count):
        h.scripted(ctx['ids'], ctx['thread'], f'Small edit {n}: add a comment line to test_sample.py', result_text=f'Added comment {n}.')


@case('e1-correction-carries-forward', 'handoff', 'Codex told "don\'t add pandas, use csv" keeps that when adding JSON export the same way',
      provider='codex', calls=1, source='handoff #2, row E1')
def _():
    def setup(h, ctx):
        seeded(h, ctx, files={'report.py': 'import csv\n\n\ndef export_csv(rows, path):\n    with open(path, "w", newline="") as fh:\n        csv.writer(fh).writerows(rows)\n'},
               grant=('greeting.py', 'test_sample.py', 'report.py'), acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Add CSV export to report.py', result_text='Added export_csv using pandas.')
        h.scripted(ctx['ids'], ctx['thread'], "Don't add pandas, use the csv module", result_text='Switched export_csv to the csv module; pandas is out.')
        unrelated(h, ctx, 2)
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Add JSON export to report.py the same way.')

    def check(h, ctx, task):
        diff = h.diff(ctx['ids'])
        return 'pandas' not in diff and has(diff, 'json'), f"pandas in diff: {'pandas' in diff}; json in diff: {has(diff, 'json')}; answer: {answer_text(task)[:60]!r}"
    return setup, action, check


@case('e4-first-message-constraint-survives-eight-messages', 'handoff', 'Message 1 said Python 3.9, no match statements; message 9 still respects it',
      provider='codex', calls=1, source='handoff #2, row E4', expect_fail=True)
def _():
    def setup(h, ctx):
        seeded(h, ctx, files={'cli.py': CLI}, grant=('greeting.py', 'test_sample.py', 'cli.py'), acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'This project must stay Python 3.9 compatible: no match statements anywhere, ever.', result_text='Understood: 3.9 only, no match statements.')
        unrelated(h, ctx, 7)
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Refactor parse_args in cli.py to use a cleaner dispatch on the command name.')

    def check(h, ctx, task):
        import re
        diff = h.diff(ctx['ids'])
        uses_match = bool(re.search(r'^\+\s*match\s+\w+.*:\s*$', diff, re.M))
        told_39 = has(transcript(task), '3.9')
        return not uses_match, f"match statement in diff: {uses_match}; transcript still says 3.9: {told_39}"
    return setup, action, check


@case('e7-failed-check-is-handed-over', 'handoff', 'Told a check failed on test_ok, Codex runs or reads the test before declaring anything done',
      provider='codex', calls=1, source='handoff #3, row E7')
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Shorten the greeting', result_text='Edited greeting.py; the check did not pass yet.',
                   tool_calls=write_tool_calls('greeting.py', GREET_BROKEN))
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Carry on.')

    def check(h, ctx, task):
        used = tools_used(task)
        checked = 'dervo_run_tests' in used
        passing = tests_pass(ctx['ids'].path)
        return checked and passing, f"ran the check: {checked}; test_sample passes now: {passing}; told about the failure: {has_any(transcript(task), 'fail', 'test_ok')}"
    return setup, action, check


@case('e9-resumed-session-knows-its-age', 'handoff', 'A Claude session continued after three days is told (and says) how old its last message is',
      provider='claude', calls=2, source='handoff #4, row E9', expect_fail=True)
def _():
    def setup(h, ctx):
        profile, model = h.agent('claude')
        ids = h.project(profile, model)
        ctx['ids'], ctx['thread'] = ids, h.thread(ids, 'chat')
        h.grant(ids)
        first = h.run(ids, ctx['thread'], 'Read greeting.py and say in one line what greet returns. Do not change anything.')
        ctx['first'] = first['id']
        ago = 3 * 86400

        def age(task):
            for message in task.get('messages') or []:
                if message.get('created_at'):
                    message['created_at'] -= ago
            for key in ('created_at', 'asked_at', 'updated_at'):
                if isinstance(task.get(key), (int, float)):
                    task[key] -= ago
            if isinstance(task.get('completion'), dict) and task['completion'].get('at'):
                task['completion']['at'] -= ago
        h.edit_task(first['id'], age)

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Before doing anything else: how long ago was my last message in this chat? Then list in plain lines what this chat decided. Change nothing.')

    def check(h, ctx, task):
        answer = answer_text(task)
        ok = has_any(answer, '3 days', 'three days', 'days ago')
        return ok, f"continuity: {told(task).get('continuity')}; answer: {answer[:100]!r}"
    return setup, action, check


@case('e14-decision-survives-six-exchanges', 'handoff', 'JSON was decided in message 2; message 9 implements the settings store without sqlite3',
      provider='codex', calls=1, source='handoff #2, row E14', expect_fail=True)
def _():
    def setup(h, ctx):
        seeded(h, ctx, files={'settings.py': '# settings store\n'}, grant=('greeting.py', 'test_sample.py', 'settings.py'), acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Should the settings store be SQLite or JSON?', result_text='Either works: SQLite gives you queries, a JSON file has no schema dependency.')
        h.scripted(ctx['ids'], ctx['thread'], 'Go with JSON, no schema dependency', result_text='Noted: the settings store will be a JSON file.')
        unrelated(h, ctx, 6)
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Implement the settings store in settings.py: load(path) and save(path, data). Keep it small.')

    def check(h, ctx, task):
        diff = h.diff(ctx['ids'])
        return 'sqlite' not in diff.casefold() and 'json' in diff.casefold(), f"sqlite in diff: {'sqlite' in diff.casefold()}; json in diff: {'json' in diff.casefold()}"
    return setup, action, check


@case('e21-yes-answers-the-question-at-the-tail', 'handoff', '"yes" after a 30,000-character answer ending "use option B?" means option B',
      provider='codex', calls=1, source='handoff #2, row E21', expect_fail=True)
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        filler = ('Considered the greeting module line by line. ' * 40 + '\n') * 22
        h.scripted(ctx['ids'], ctx['thread'], 'Review greeting.py and propose how to add a farewell',
                   result_text=filler + '\nTwo options. Option A: a separate farewell module. Option B: a farewell function in greeting.py next to greet. Want me to use option B?')
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'yes')

    def check(h, ctx, task):
        answer, diff = answer_text(task), h.diff(ctx['ids'])
        did_b = has(diff, 'farewell') and 'greeting.py' in diff and not has_any(answer, 'which option', 'what do you mean', 'yes to what', 'clarify')
        return did_b, f"farewell in greeting.py diff: {has(diff, 'farewell') and 'greeting.py' in diff}; asked back: {has_any(answer, 'which option', 'what do you mean', 'clarify')}; answer: {answer[:80]!r}"
    return setup, action, check


@case('e22-read-only-finding-carries-into-the-fix', 'handoff', 'A read-only question that found the bug in utils/dates.py, then "fix it": the fix goes straight to that file',
      provider='codex', calls=2, source='handoff row E22')
def _():
    def setup(h, ctx):
        profile, model = h.agent('codex')
        ids = h.project(profile, model, files={'utils/dates.py': DATES, 'utils/__init__.py': ''})
        ctx['ids'], ctx['thread'] = ids, h.thread(ids, 'chat')
        ctx['first'] = h.run(ids, ctx['thread'], 'Which function in utils/dates.py drops the timezone? Answer in one line, change nothing.')
        h.grant(ids, files=('utils/dates.py', 'greeting.py', 'test_sample.py'))

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'fix it')

    def check(h, ctx, task):
        diff = h.diff(ctx['ids'])
        used = tools_used(task)
        first_read = next((t for t in used if t in ('dervo_read_file', 'dervo_list_files')), None)
        return 'utils/dates.py' in diff, f"utils/dates.py in diff: {'utils/dates.py' in diff}; first look: {first_read}; tools: {used[:4]}"
    return setup, action, check


# ---------------------------------------------------------------------- hard cases, 20 Sep (notes/smartness/hard-cases-20260920.md)
# A goal you abandoned, a constraint you lifted, a decision buried under twenty exchanges, "fix it" after two findings,
# "it" with a decoy antecedent, the first message's constraint past the handoff budget, and a file another chat changed.

HARD = 'hard-cases 20 Sep'
FILLER = ('Looked over the module once more and left the structure as it was, since nothing there needed to move for this edit. '
          'The comment now says what the test covers and the sample test still passes. Nothing else in the folder was touched. ')


def unrelated_long(h, ctx, count, start=1):
    """Exchanges long enough that ``count`` of them pass the handoff budget (6,000 characters, cut from the middle)."""
    for n in range(start, start + count):
        h.scripted(ctx['ids'], ctx['thread'], f'Small edit {n}: add a comment line to test_sample.py', result_text=f'Added comment {n}. ' + FILLER * 6)


@case('e40-abandoned-goal-is-not-handed-on', 'handoff', 'After "drop the title idea entirely", the next message is not told "Its goal: greet takes a title"',
      source=HARD + ', you contradict an earlier goal')  # fixed 21 Sep: a borrowed goal stops at words that call it off (session_continuity.abandons)
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Let greet take a title', result_text='greet takes a title now.',
                   guide=dict(goal='greet takes a title', step='Added the title parameter', next='Update the test'))
        h.scripted(ctx['ids'], ctx['thread'], 'Drop the title idea entirely, we are not doing it. Put greeting.py back as it was.',
                   result_text='Dropped it; greeting.py is back as it was, no title parameter.')

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Summarise where we are', result_text='Summary.')

    def check(h, ctx, task):
        text = transcript(task)
        stale = has(text, 'Its goal: greet takes a title')
        return not stale, f"stale goal line: {stale}; 'title' in transcript: {has(text, 'title')}"
    return setup, action, check


@case('e41-decision-survives-twenty-exchanges', 'handoff', 'JSON was decided in message 2; message 23, past the handoff budget, still implements the store without sqlite3',
      provider='codex', calls=1, source=HARD + ', scale, handoff row E14 at twenty')
def _():
    def setup(h, ctx):
        seeded(h, ctx, files={'settings.py': '# settings store\n'}, grant=('greeting.py', 'test_sample.py', 'settings.py'), acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Should the settings store be SQLite or JSON?', result_text='Either works: SQLite gives you queries, a JSON file has no schema dependency.')
        h.scripted(ctx['ids'], ctx['thread'], 'Go with JSON, no schema dependency', result_text='Noted: the settings store will be a JSON file.')
        unrelated_long(h, ctx, 20)
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Implement the settings store in settings.py: load(path) and save(path, data). Keep it small.')

    def check(h, ctx, task):
        diff = h.diff(ctx['ids'])
        told_json = has(transcript(task), 'JSON')
        return 'sqlite' not in diff.casefold() and 'json' in diff.casefold(), f"sqlite in diff: {'sqlite' in diff.casefold()}; json in diff: {'json' in diff.casefold()}; transcript still says JSON: {told_json}; transcript {len(transcript(task))} chars"
    return setup, action, check


@case('e42-lifted-constraint-does-not-bind', 'handoff', 'Message 1 said keep greeting.py under five lines, message 4 lifted it; the docstring and farewell go in',
      provider='codex', calls=1, source=HARD + ', you contradict an earlier instruction')
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Keep greeting.py under five lines, always. No docstrings, no extra functions.', result_text='Understood: greeting.py stays under five lines.')
        unrelated(h, ctx, 2)
        h.scripted(ctx['ids'], ctx['thread'], 'Forget the five-line rule for greeting.py, it was silly. Files can be any length now.', result_text='Understood, the five-line limit is gone.')
        unrelated(h, ctx, 2, start=3)
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'Add a one-line docstring to greet and a farewell(name) function below it in greeting.py.')

    def check(h, ctx, task):
        diff, answer = h.diff(ctx['ids']), answer_text(task)
        added = has(diff, 'def farewell') and '"""' in diff
        held_back = has_any(answer, 'five lines', 'five-line', '5 lines', '5-line', 'under five')
        return added and not held_back, f"farewell and docstring in diff: {added}; answer still cites the five-line rule: {held_back}; answer: {answer[:90]!r}"
    return setup, action, check


@case('e43-fix-it-after-two-findings-asks-which', 'handoff', 'A read-only run found two bugs in two files; "fix it" asks which one and changes nothing',
      provider='codex', calls=1, source=HARD + ', ambiguity that should ask')
def _():
    def setup(h, ctx):
        seeded(h, ctx, files={'utils/dates.py': DATES, 'utils/__init__.py': '', 'cli.py': CLI_BARE_ARGV}, grant=('greeting.py', 'test_sample.py', 'utils/dates.py', 'cli.py'), acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'Look for bugs in utils/dates.py and cli.py. Change nothing.',
                   result_text='Two findings. parse_date in utils/dates.py drops the timezone offset. parse_args in cli.py raises IndexError on an empty argv instead of returning help.')
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'fix it')

    def check(h, ctx, task):
        diff, answer = h.diff(ctx['ids']), answer_text(task)
        asked = '?' in answer and has_any(answer, 'which', 'both', 'timezone', 'argv', 'dates.py', 'cli.py')
        return asked and not diff.strip(), f"asked: {asked}; changed files: {[l for l in diff.splitlines() if l.startswith(('+++', 'diff --git'))][:4]}; answer: {answer[:100]!r}"
    return setup, action, check


@case('e44-it-means-the-function-from-message-one', 'handoff', '"add it now" means the farewell function named two messages earlier, not the comment from the message between',
      provider='codex', calls=1, source=HARD + ', three-step with a decoy')
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'When we add a goodbye function to greeting.py later, name it farewell, never goodbye.', result_text='Noted: farewell, not goodbye, when the time comes.')
        h.scripted(ctx['ids'], ctx['thread'], 'Add a one-line comment at the top of test_sample.py saying what it covers.', result_text='Added the comment at the top of test_sample.py.')
        probe_on(h, ctx, 'codex')

    def action(h, ctx):
        return h.run(ctx['ids'], ctx['thread'], 'add it now')

    def check(h, ctx, task):
        diff, answer = h.diff(ctx['ids']), answer_text(task)
        farewell, goodbye = has(diff, 'def farewell'), has(diff, 'def goodbye')
        return farewell and not goodbye, f"def farewell in diff: {farewell}; def goodbye: {goodbye}; asked back: {'?' in answer}; answer: {answer[:80]!r}"
    return setup, action, check


@case('e45-first-constraint-in-the-transcript-after-twenty-five', 'handoff', 'Message 1 said Python 3.9; twenty-five long exchanges later the handoff still carries it',
      source=HARD + ', scale, handoff budget')
def _():
    def setup(h, ctx):
        seeded(h, ctx, acceptance=False)
        h.scripted(ctx['ids'], ctx['thread'], 'This project must stay Python 3.9 compatible: no match statements anywhere, ever.', result_text='Understood: 3.9 only, no match statements.')
        unrelated_long(h, ctx, 25)

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Summarise the constraints on this project', result_text='Summary.')

    def check(h, ctx, task):
        text = transcript(task)
        return has(text, '3.9'), f"'3.9' in transcript: {has(text, '3.9')}; {len(text)} chars; left-out marker: {has(text, 'left out')}"
    return setup, action, check


@case('e46-file-changed-by-another-chat-is-told', 'handoff', 'Chat A read greeting.py; chat B changed and committed it; A\'s next message is told another chat changed the file',
      source=HARD + ', stale fact across chats, handoff row E8')
def _():
    def setup(h, ctx):
        ids = seeded(h, ctx, acceptance=False)
        h.scripted(ids, ctx['thread'], 'Read greeting.py and tell me what greet returns. Change nothing.', result_text='greet returns "Hello, <name>".',
                   tool_calls=[dict(name='dervo_read_file', arguments=dict(path='greeting.py'))])
        ctx['b'] = h.thread(ids, 'B')
        h.grant(ids, thread=ctx['b'])
        h.call('coding_acceptance_set', projectId=ids.project, channelId=ctx['b'], required=True)
        task = h.scripted(ids, ctx['b'], 'Make greet say Hello there', result_text='Changed greeting.py.',
                          tool_calls=write_tool_calls('greeting.py', 'def greet(name):\n    return f"Hello, {name}"  # changed in chat B\n'))
        h.review(ids, ctx['b'], task, 'approved')
        try:
            h.commit(ids, ctx['b'], task)
        except Exception:
            pass  # approval already committed

    def action(h, ctx):
        return h.scripted(ctx['ids'], ctx['thread'], 'Now add a farewell function next to greet', result_text='Added farewell.')

    def check(h, ctx, task):
        text = transcript(task)
        told_change = has_any(text, 'another chat', 'other chat', 'another thread', 'other thread', 'changed since', 'changed by', 'since you last read', 'changed greeting.py')
        return told_change, f"told another chat changed it: {told_change}; 'greeting.py' in transcript: {has(text, 'greeting.py')}"
    return setup, action, check


CASES = REGISTRY['handoff']
