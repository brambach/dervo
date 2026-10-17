"""Polisher-only stand-in for Claude Code. Home's one-shot calls (-p with --json-schema) get a scripted structured answer
from $CLAUDE_CONFIG_DIR/home-rules.json ([{"match": regex, "answer": {...}}], first match on the prompt's last request
wins); everything else runs backend/fake_claude_cli.py unchanged. Nothing here calls a model."""
import json, os, re, sys, time
from pathlib import Path

FAKE = Path(__file__).resolve().parents[2] / 'backend' / 'fake_claude_cli.py'
EMPTY = dict(reply='', looked_at=[], mentions=[], actions=[], offers=[], filing=[], workstreams=[], log=[], rule='',
             question=dict(ask='', options=[]))

argv = sys.argv[1:]
if '-p' in argv and '--json-schema' in argv:
    prompt = sys.stdin.read()
    home = os.environ.get('CLAUDE_CONFIG_DIR', '')
    with open(os.path.join(home, 'calls.jsonl'), 'a') as fh:
        fh.write(json.dumps({'argv': argv, 'home': True, 'prompt_tail': prompt[-3000:]}) + '\n')
    try:
        rules = json.loads(Path(home, 'home-rules.json').read_text())
    except (OSError, ValueError):
        rules = []
    # The arrival summary: on a first visit there's no last time to compare with, and the screen's own lead already
    # says where the threads stand, so it adds nothing.
    answer = dict(EMPTY, reply='' if 'This is their first visit' in prompt else 'Nothing new since you were last here.')
    said = prompt.rsplit('The person says:', 1)[-1] if 'The person says:' in prompt else ''
    for rule in rules:
        if said and re.search(rule['match'], said, re.S | re.I):
            answer = dict(EMPTY, **rule['answer'])
            time.sleep(rule.get('delay', 0))
            break
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': answer['reply'],
                      'structured_output': answer, 'usage': {'input_tokens': 10, 'output_tokens': 10}, 'total_cost_usd': 0}))
    sys.exit(0)
# ``polish_write`` ({path, text}) in fake-scenario.json: a coding run edits a file under its working folder first, the
# way a full-access Claude Code would, so the result has changes to review (and checks to offer).
try:
    write = json.loads(Path(os.environ.get('CLAUDE_CONFIG_DIR', ''), 'fake-scenario.json').read_text()).get('polish_write')
except (OSError, ValueError):
    write = None
if isinstance(write, dict) and 'auth' not in argv:
    Path(os.getcwd(), write['path']).write_text(write['text'])
os.execv(sys.executable, [sys.executable, str(FAKE), *argv])
