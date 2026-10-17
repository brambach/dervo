"""Does Jev beat the rules? An experiment, outside the app, that touches nothing the app ships.

Bryce, 20 Sep 2026: TypeSafe's Jev returns typed, calibrated decisions instead of text, in 70-500 ms, at $0.042 per
million input tokens. Dervo already makes a lot of those decisions with regexes and wordlists that get patched every
time an eval finds one wrong: ``project_orchestrator.asks_for_work`` and friends, and the filing cascade in
``project_ideas.file_by_rules``. This script runs both over the same cases and prints who was right, so the decision
to wire Jev in (or not) is made on our own data rather than TypeSafe's benchmark.

Nothing here imports the store or the Host, nothing writes, and no thread content leaves this machine unless you pass
a key. ``--dry-run`` prints the exact request bodies and stops.

    python3 tools/jev_eval.py --dry-run                 # see what would be sent, no key needed
    TYPESAFE_API_KEY=sk-... python3 tools/jev_eval.py   # run both tasks for about half a cent

The two tasks:

* ``orchestrator`` sends each message once with five nouls attached, the way Home would, so the batching claim gets
  tested and not just the accuracy. Labels for ``asks_for_work`` come straight from test_project_orchestrator.py, so
  they are the same human calls the rules were built against. The other four have no labels, so those report
  agreement with the rules and list the disagreements for you to read.
* ``filing`` is a Choice over the workstreams that exist, plus "new workstream", against the ten-thread week in
  test_thread_filing.py. The rules score 9/9 there because the fixtures are the ones they were tuned on, so a second
  set of requests the rules have never seen is included and marked. Read that one, not the first.

The interesting number is not accuracy at a 0.5 cut. It is whether an abstain band exists: act silently when Jev is
confident, suggest when it isn't, leave the rules in charge at the bottom. ``--bands`` prints that table.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / 'backend'
DEFAULT_URL = 'https://api.typesafe.ai/v1/systemone'
DEFAULT_MODEL = 'jev-latest'
INPUT_PRICE = 0.042 / 1_000_000  # per input token, 20 Sep 2026; output is free
RETRY_STATUS = (429, 529)


# ---------------------------------------------------------------------------- fixtures

# Every case below is copied from the test suite with its label, so the rules are being read against the same human
# calls they were written to pass. The source file and line are in the comment above each block.

# test_project_orchestrator.py, test_what_asks_for_work + test_a_request_for_several_pieces_of_work_is_not_a_report
# + test_a_heading_with_nothing_under_it_asks_for_nothing. True means the message asks for work to start.
FIVE = ('Five quick fixes, each on its own: 1) the Settings tab label is misspelled, 2) the About dialog has no close '
        'button, 3) the changelog link is dead, 4) the tooltip on Push is cut off, 5) the release planner ignores '
        'keyboard focus.')
ASKS_FOR_WORK = [
    ('the send arrow is off centre, fix it', True),
    ('Fix the send arrow, it sits low.', True),
    ('can you give the sidebar more room?', True),
    ('the sidebar jumps - look into it', True),
    ('The list is slow so speed it up', True),
    ('Voice on Home again, please.', True),
    ('export crashes and needs fixing', True),
    ("let's tidy the tray", True),
    ('the send arrow is off centre', False),
    ('The sidebar jumps when a run finishes', False),
    ('Keychain export is broken for me.', False),
    ('The tray covers send, the clip icon is blurry, and paste from Finder would help.', False),
    ('Idea: voice chat on Home would be great.', False),
    ('switching threads loses my draft again', False),
    (FIVE, True),
    (FIVE.replace(', each on its own', ''), True),
    ('five things: the tray covers send, the clip is blurry', True),
    ('Here are three fixes for the sidebar:\n- rows are cramped\n- the badge overlaps', True),
    ('To do:\n- the tray covers send\n- the clip icon is blurry', True),
    ('Tasks: the tray, the clip', True),
    ('A couple of small tweaks: the tray covers send, the clip icon is blurry', True),
    ('I need the tray moved, the clip sharper and paste from Finder', True),
    ('we need to centre the arrow', True),
    ('I want dark mode on Home', True),
    ('the tray covers send and the clip is blurry, one job each', True),
    ('Ideas:\n- dark mode for Home\n- a shortcut for Push\n- Catch up as Markdown', False),
    ('Bugs:\n- tray\n- clip', False),
    ('- the clip icon is blurry\n- paste from Finder would help', False),
    ('Some changes broke the build', False),
    ('Three things are broken on Home', False),
    ('Two tasks crashed overnight.', False),
    ('the tests run in parallel and flake', False),
    ('The send arrow is off centre', False),
    ('Tasks: none today', False),
    ('Standup. Done: the tray. Tasks: none today.', False),
    ('To do: nothing yet', False),
    ('Tasks:\nnone', False),
    ('Jobs: n/a. The clip icon is blurry.', False),
    ('Fixes: none so far, the clip icon is blurry', False),
    ('Tasks: the tray, the clip', True),
    ('Tasks: none today, but fix the clip', True),
    ('To do:\n- the tray covers send', True),
    ('Tasks: nothing yet. Can you centre the arrow?', True),
]

# The five questions Home asks about every message, as nouls. Only asks_for_work has labels; the rest are here to
# prove the batching claim (adding four questions should barely move the latency) and to surface disagreements.
MIXED = [
    # test_project_orchestrator.py:1129-1131, the three yes_to_offer cases.
    ('yes fix it', dict(yes_to_offer=True)),
    ('yes, and push it to main', dict(yes_to_offer=False)),
    ('fix the sidebar', dict(yes_to_offer=False)),
    ('ok do it', dict(yes_to_offer=True)),
    ('sounds good', dict(yes_to_offer=False)),  # a reaction, not a go (the OFFER_YES comment says so)
    # test_project_orchestrator.py:1263-1264 and the CONVERSATIONAL comment above standing_rule.
    ('only use the staging database for tests', dict(standing_rule=True)),
    ('never mind', dict(standing_rule=False)),
    ("don't worry about it", dict(standing_rule=False)),
    ('always run the check before you push', dict(standing_rule=True)),
    ('push it later', dict(standing_rule=False)),
    # The FOLLOW_UP comment, eval h6 of 20 Sep.
    ('that one too', dict(follow_up=True)),
    ('same for the login screen', dict(follow_up=True)),
    ('and that', dict(follow_up=True)),
    ('start a fresh thread for the installer', dict(follow_up=False)),
    # only_questions: a real question against a request wearing a question mark.
    ('what did the last run change?', dict(only_questions=True)),
    ('can you fix it?', dict(only_questions=False, asks_for_work=True)),
    ('which account is this thread on? and how much is left on it?', dict(only_questions=True)),
    ('why is the sidebar cramped? tighten it.', dict(only_questions=False)),
]

HOME_QUESTIONS = {
    'asks_for_work': dict(
        type='noul',
        instructions=('The person is talking to Dervo about their software project. Does any part of this message ask '
                      'for work to start now? Reporting a bug, listing ideas or describing what is wrong is not '
                      'asking. A heading with nothing under it ("Tasks: none today") is not asking. A list of fixes '
                      'someone clearly wants done is asking, even without a please.'),
        criteria={'true': 'Some clause asks for work to be done now.',
                  'false': 'It only reports, describes, lists ideas, or says nothing needs doing.'}),
    'only_questions': dict(
        type='noul',
        instructions=('Is every sentence of this message a genuine question seeking an answer, rather than a request '
                      'phrased as one? "Can you fix it?" is a request, not a question.')),
    'yes_to_offer': dict(
        type='noul',
        instructions=('Dervo has just offered to start a piece of work. Is this reply a plain yes to that offer and '
                      'nothing more? "yes", "ok do it", "fix it" are. "yes, and push it to main" is not, because it '
                      'adds a new instruction. "sounds good" on its own is a reaction, not a go.')),
    'standing_rule': dict(
        type='noul',
        instructions=('Does this state a standing rule for the project, meant to hold from now on, rather than a '
                      'one-off step, a deferral or a reply? "Only use the staging database for tests" is a rule. '
                      '"Never mind" is not.')),
    'follow_up': dict(
        type='noul',
        instructions=('Does this point back at something from the previous reply ("that one too", "same for the '
                      'login screen") rather than standing on its own?')),
}
RULE_FUNCTIONS = ('asks_for_work', 'only_questions', 'yes_to_offer', 'standing_rule', 'follow_up')


def cases():
    """Every message, with whichever of the five it carries a human label for."""
    return [(text, dict(asks_for_work=expected)) for text, expected in ASKS_FOR_WORK] + MIXED

# test_thread_filing.py, TenThreadsTests: Bryce's real week of 18 Sep and where the rules put each thread. The rules
# score 9/9 here by construction, since these are the fixtures they were tuned against. Read HELD_OUT instead.
TUNED_WORKSTREAMS = ['Orchestrator', 'Design', 'Connector', 'Authentication', 'Releases', 'Knowledge']
TUNED_FILING = [
    ('Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped, with the idea as the group key '
     'for conversations.', 'Orchestrator'),
    ('Ideally I want to see this thing working and see the orchestrator working in real time so I can tell what each '
     'job is doing', 'Orchestrator'),
    ('I think it would be good to do a full design sweep as a really critical design person who is looking at every '
     'screen', 'Design'),
    ('the connector picker should warn when a server isn’t allowed in this project', 'Connector'),
    ('Tighten the sidebar spacing between groups and make the selected row frosted', 'Design'),
    ('Fix login: the session expires after an hour and people have to sign in again', 'Authentication'),
    ('After a beta downloads, offer Update and restart in the sidebar footer instead of a dialog', 'Releases'),
    ('Add the release planner screen for the owner: four lists, in this beta, built for the next beta, people want, '
     'planned', 'Releases'),
    ('Knowledge page feels scattered, make it one clear page with guidance, lessons and starter procedures',
     'Knowledge'),
]

# NOT from the test suite. Written 20 Sep against the same six workstreams, labelled by what a person would obviously
# expect. These are the ones that matter: the rules have never seen them, and several are built to miss the word
# overlap the cascade leans on ("the app forgets who I am", "ship it to the testers").
HELD_OUT_FILING = [
    ('The app forgets who I am every morning and makes me start over', 'Authentication'),
    ('Ship it to the testers on Tuesday and put the notes in the footer', 'Releases'),
    ('Rows are too tight and the selected one is hard to see', 'Design'),
    ('The MCP server list takes four seconds to appear the first time', 'Connector'),
    ('Home keeps starting a job when I only asked it a question', 'Orchestrator'),
    ('Write down what we learned about the pnpm trap so nobody repeats it', 'Knowledge'),
    ('Token refresh fails silently and the next request 401s', 'Authentication'),
    ('The changelog link in the About dialog is dead', 'Releases'),
    ('Home should put "that one too" on the thread it just offered', 'Orchestrator'),
    ('Typography on the composer footer is a size too small', 'Design'),
]
NEW_WORKSTREAM = '__new__'


# ---------------------------------------------------------------------------- transport

class Jev:
    """One call: state plus a map of named typed questions, answered in parallel. See docs.typesafe.ai/api."""

    def __init__(self, key, url=DEFAULT_URL, model=DEFAULT_MODEL, dry_run=False):
        self.key, self.url, self.model, self.dry_run = key, url, model, dry_run
        self.latencies, self.input_tokens, self.calls = [], 0, 0

    def ask(self, state, questions):
        body = json.dumps(dict(state=state, model=self.model, questions=questions)).encode()
        if self.dry_run:
            return dict(answers={}, usage=dict(input_tokens=0), _body=body)
        request = urllib.request.Request(self.url, data=body, method='POST', headers={
            'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'})
        for attempt in range(4):
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    answer = json.loads(response.read())
            except urllib.error.HTTPError as error:
                if error.code in RETRY_STATUS and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                detail = error.read().decode(errors='replace')[:400]
                raise SystemExit(f'Jev returned {error.code}: {detail}')
            self.latencies.append((time.monotonic() - started) * 1000)
            self.input_tokens += (answer.get('usage') or {}).get('input_tokens') or 0
            self.calls += 1
            return answer
        raise SystemExit('Jev kept returning 429/529')

    def report(self):
        if not self.latencies:
            return 'no calls made'
        ordered = sorted(self.latencies)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        return (f'{self.calls} calls, p50 {statistics.median(ordered):.0f} ms, p95 {p95:.0f} ms, '
                f'{self.input_tokens} input tokens, ${self.input_tokens * INPUT_PRICE:.5f}')


def run_all(jev, states, questions, workers):
    """Each state gets one call carrying every question. Jev evaluates them in parallel inside the request."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda state: jev.ask(state, questions), states))


# ---------------------------------------------------------------------------- scoring

def band_table(pairs, label):
    """Accuracy against coverage if you abstain in the middle. ``pairs`` is (probability, expected bool).

    This is the table that decides whether Jev is usable at all. A model that is 85% right on everything is no use.
    One that is 99% right on the 70% it is confident about, and hands the rest back to the rules, is.
    """
    lines = [f'  abstain band        acts on   right when it acts   ({label})']
    for low, high in ((0.5, 0.5), (0.4, 0.6), (0.3, 0.7), (0.2, 0.8), (0.1, 0.9), (0.05, 0.95)):
        acted = [(p, e) for p, e in pairs if p <= low or p >= high]
        if not acted:
            continue
        right = sum(1 for p, e in acted if (p >= 0.5) == e)
        lines.append(f'  {low:.2f} to {high:.2f}      {len(acted) / len(pairs):>6.0%}   '
                     f'{right}/{len(acted)} = {right / len(acted):>4.0%}')
    return '\n'.join(lines)


def baseline_only(rules):
    """The rules alone, no network, no key. What Jev has to beat, and every function exercised here so a real run
    can't fall over on a missing name three minutes and forty calls in."""
    for name in RULE_FUNCTIONS:
        function = getattr(rules, name)
        labelled = [(text, labels[name]) for text, labels in cases() if name in labels]
        right = sum(1 for text, expected in labelled if bool(function(text)) == expected)
        print(f'rules, {name}: {right}/{len(labelled)} = {right / len(labelled):.0%} on their own fixtures')
    print('\nThe filing cascade is not run here: it needs a store and a Host. Jev is scored against the workstreams '
          'the filing tests assert, which the rules pass.')


def orchestrator_task(jev, args, rules):
    print('\n=== Home\'s yes/no questions: five nouls per message, one call each ===')
    every = cases()
    if jev.dry_run:
        print(json.dumps(json.loads(jev.ask(every[0][0], HOME_QUESTIONS)['_body']), indent=2)[:2000])
        print(f'\n({len(every)} such calls would be sent for this task)')
        return
    answers = run_all(jev, [text for text, _ in every], HOME_QUESTIONS, args.workers)

    for name in RULE_FUNCTIONS:
        function = getattr(rules, name)
        scored = [(text, labels[name], answer['answers'][name]['noul'])
                  for (text, labels), answer in zip(every, answers) if name in labels]
        rules_right = sum(1 for text, expected, _ in scored if bool(function(text)) == expected)
        jev_right = sum(1 for _, expected, p in scored if (p >= args.threshold) == expected)
        total = len(scored)
        print(f'\n{name}, {total} labelled cases')
        print(f'  rules  {rules_right}/{total} = {rules_right / total:.0%}')
        print(f'  Jev    {jev_right}/{total} = {jev_right / total:.0%}  (cut at {args.threshold})')
        wrong = [(t, e, bool(function(t)), p) for t, e, p in scored
                 if (p >= args.threshold) != e or bool(function(t)) != e]
        if wrong:
            print('  where they part company (label / rules / Jev):')
            for text, expected, by_rules, probability in wrong:
                print(f'    {str(expected):<5} {str(by_rules):<5} {probability:.3f}  '
                      f'{text.replace(chr(10), " · ")[:68]}')
        if args.bands and total >= 20:
            print(band_table([(p, e) for _, e, p in scored], name))

    print('\nUnlabelled: what Jev says about the other four on the 43 asks_for_work messages, where it disagrees '
          'with the rules. These have no ground truth, so read them, don\'t score them.')
    for name in RULE_FUNCTIONS[1:]:
        function = getattr(rules, name)
        differ = [(text, bool(function(text)), answer['answers'][name]['noul'])
                  for (text, labels), answer in zip(every, answers)
                  if name not in labels and (answer['answers'][name]['noul'] >= args.threshold) != bool(function(text))]
        print(f'  {name}: {len(differ)} disagreements')
        for text, by_rules, probability in differ[:5]:
            print(f'      rules {str(by_rules):<5} Jev {probability:.3f}  {text.replace(chr(10), " ")[:62]}')


def filing_task(jev, args):
    print('\n=== Filing: one Choice over the workstreams that exist, plus a new one ===')
    # Thin descriptions on purpose: the fixtures carry no summaries. In the app a workstream has ``summary``
    # (project_ideas) and the names of the threads already in it, and both belong here. If Jev does badly on this
    # task, try that before concluding anything, because a Choice is only as good as what its criteria say.
    criteria = {name: f'Work on {name.lower()}' for name in TUNED_WORKSTREAMS}
    criteria[NEW_WORKSTREAM] = 'None of the existing workstreams fit; this needs a new one'
    questions = {'workstream': dict(
        type='choice',
        instructions=('This is the first message of a new thread in a software project. Which existing workstream '
                      'does this work belong in? Choose a new workstream only when none of them genuinely fit.'),
        criteria=criteria)}

    for label, cases, note in (('tuned (the rules\' own fixtures, they score 9/9 here)', TUNED_FILING, ''),
                               ('held out (new, never seen by the rules)', HELD_OUT_FILING, ' <- the real test')):
        if jev.dry_run:
            print(json.dumps(json.loads(jev.ask(cases[0][0], questions)['_body']), indent=2)[:1800])
            return
        answers = run_all(jev, [text for text, _ in cases], questions, args.workers)
        right, rows = 0, []
        for (text, expected), answer in zip(cases, answers):
            got = answer['answers']['workstream']
            right += got['choice'] == expected
            rows.append((text, expected, got['choice'], got.get('confidence') or 0.0))
        print(f'\n  {label}{note}')
        print(f'  Jev {right}/{len(cases)} = {right / len(cases):.0%}')
        for text, expected, choice, confidence in rows:
            mark = ' ' if choice == expected else 'x'
            print(f'    {mark} {expected:<16} {choice:<16} conf {confidence:.2f}  {text[:56]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--task', choices=('orchestrator', 'filing', 'both'), default='both')
    parser.add_argument('--dry-run', action='store_true', help='print the request bodies and stop, no key needed')
    parser.add_argument('--baseline', action='store_true', help='run the rules only, no network, no key')
    parser.add_argument('--threshold', type=float, default=0.5, help='where a noul becomes a yes')
    parser.add_argument('--bands', action='store_true', help='accuracy against coverage if you abstain in the middle')
    parser.add_argument('--workers', type=int, default=6, help='calls in flight at once')
    parser.add_argument('--base-url', default=os.environ.get('JEV_URL', DEFAULT_URL),
                        help='override for a gateway; OpenRouter needs its own path, check before using it')
    parser.add_argument('--model', default=os.environ.get('JEV_MODEL', DEFAULT_MODEL))
    args = parser.parse_args()

    key = os.environ.get('TYPESAFE_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
    if not key and not (args.dry_run or args.baseline):
        raise SystemExit('Set TYPESAFE_API_KEY (or OPENROUTER_API_KEY with --base-url), or pass --dry-run/--baseline.')

    sys.path.insert(0, str(BACKEND))
    import project_orchestrator as rules

    if args.baseline:
        return baseline_only(rules)

    jev = Jev(key, url=args.base_url, model=args.model, dry_run=args.dry_run)
    if args.task in ('orchestrator', 'both'):
        orchestrator_task(jev, args, rules)
    if args.task in ('filing', 'both'):
        filing_task(jev, args)
    if not args.dry_run:
        print(f'\n{jev.report()}')


if __name__ == '__main__':
    main()
