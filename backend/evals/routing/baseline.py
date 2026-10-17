"""Write the current scores as the baseline the gate holds (test_thread_routing). Run it when a change scores better,
in the same commit, never to let a worse score through."""
import json
import pathlib
import sys

from evals.routing import score

PATH = pathlib.Path(__file__).resolve().parent / 'baseline.json'
COMMITTED = ('dev', 'heldout', 'test')


def main(argv=None):
    # --accept-worse: a deliberate trade (say, fewer silent mistakes for one more message sent to the model). Say why in
    # the commit message.
    accept = '--accept-worse' in (sys.argv[1:] if argv is None else argv)
    results = score.run(COMMITTED)
    old = json.loads(PATH.read_text()) if PATH.exists() else {}
    new = {suite: {k: data['score'][k] for k in ('messages', 'accuracy', 'misroutes', 'naming', 'auto_wrong')} for suite, data in results.items()}
    for suite, values in new.items():
        before = old.get(suite)
        if not accept and before and before.get('messages') == values['messages'] and (
                values['accuracy'] < before['accuracy'] or values['misroutes'] > before['misroutes'] or values['auto_wrong'] > before.get('auto_wrong', 1)):
            print(f'{suite} scores worse than its baseline; not writing. Pass --accept-worse for a deliberate trade.', file=sys.stderr)
            return 1
    PATH.write_text(json.dumps(new, indent=1) + '\n')
    print(json.dumps(new, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
