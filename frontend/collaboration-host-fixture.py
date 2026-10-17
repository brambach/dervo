"""Three-role browser fixture for cross-channel collaboration. Real Host, CodingTasks, collaboration broker, evidence,
file writes and confined tests on a disposable project; the providers are the synthetic Codex app-server peer and the
synthetic Claude stream-json peer from backend/test_collaboration.py, scripted by the role named in the payload the host
actually delivered. Nothing here launches a real provider, reads credentials or touches anything outside the temp dirs.

argv: <data dir> <project dir>. Extra operations (never bridge operations): ``_picked_project``, ``_fixture_starts``
(provider turn starts as [provider, profile id] pairs), ``_fixture_scenario {name}`` (review-and-fix, refusal, hang)."""
import json
import os
import select
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from host import Host  # noqa: E402
import test_collaboration as tc  # noqa: E402

project = Path(sys.argv[2])
scenario = {'name': 'review-and-fix'}
case = tc.CollaborationTests()
case.peers, case.starts, case.inputs = [], [], {}


def script(peer, text):
    role = tc.CollaborationTests.role_of(text)
    mode = scenario['name']
    if mode == 'hang' and role in ('reviewer', 'worker'):
        return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('hang',)]
    if role == 'reviewer':
        return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('finish', 'REVIEW: greet() returns "hi"; the test expects "hi". Fine. Line 1.')]
    if role == 'worker':
        current = (project / 'greeting.py').read_bytes()
        return [('tool', 'dervo_read_file', {'path': 'greeting.py'}),
                ('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': tc.sha(current), 'content': current.decode() + '# fixed by helper\n'}),
                ('tool', 'dervo_run_tests', {}), ('finish', 'FIX: added a comment; tests pass.')]
    if mode == 'refusal':
        return [('tool', 'dervo_request_help', {'agent': 'fixer', 'purpose': 'Edit greeting.py', 'instructions': '', 'files': ['greeting.py']}),
                ('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'Look at secret_notes.py', 'instructions': '', 'files': ['secret_notes.py']}),
                ('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'Review greeting.py', 'instructions': '', 'files': ['greeting.py']}),
                ('finish', 'Asked what was allowed.')]
    if mode == 'hang':
        return [('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'Review greeting.py slowly', 'instructions': '', 'files': ['greeting.py']}, 'parallel'),
                ('tool', 'dervo_request_help', {'agent': 'fixer', 'purpose': 'Fix greeting.py slowly', 'instructions': '', 'files': ['greeting.py']}),
                ('finish', 'never reached')]
    return [('tool', 'dervo_read_file', {'path': 'test_sample.py'}),
            ('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'Review greeting.py against test_sample.py', 'instructions': 'Say if greet() matches the test.', 'files': ['greeting.py', 'test_sample.py']}, 'parallel'),
            ('tool', 'dervo_request_help', {'agent': 'fixer', 'purpose': 'Add a trailing comment to greeting.py and keep the tests green', 'instructions': 'Append one comment line.', 'files': ['greeting.py']}),
            ('tool', 'dervo_run_tests', {}), ('finish', 'Integrated the review and the fix; tests pass.')]


patches = case.peer_patch(script)
patches.__enter__()
host = Host(Path(sys.argv[1]) / 'store')
try:
    buffer = b''
    while True:
        host.tick()
        if b'\n' not in buffer:
            if not select.select([sys.stdin], [], [], .02)[0]:
                continue
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            if b'\n' not in buffer:
                continue
        line, buffer = buffer.split(b'\n', 1)
        try:
            request = json.loads(line)
            op, args = request['operation'], request['args']
            if op == '_picked_project':
                result = host.add_picked_project(**args)
            elif op == '_fixture_starts':
                result = [list(s) for s in case.starts]
            elif op == '_fixture_scenario':
                scenario['name'] = args['name']
                result = dict(scenario)
            else:
                result = host.dispatch(op, args)
            response = {'result': result}
        except Exception as exc:
            response = {'error': str(exc)}
        print(json.dumps(response), flush=True)
finally:
    host.close()
    patches.__exit__(None, None, None)
