"""FAKE Claude Code CLI for backend tests only. Speaks the stream-json shapes the harness worker expects; never infers.

It reads a scenario from ``$CLAUDE_CONFIG_DIR/fake-scenario.json`` (the worker passes an allowlisted environment, so
the config dir is the one channel a test controls), records every invocation in ``calls.jsonl`` and every stdin
message in ``received.jsonl`` next to it.

Text-only scenario keys: ``auth`` (the ``auth status`` JSON), ``init_model`` (model reported on the system init),
``result_text``, ``mode`` (``ok`` or ``hang``), ``hang_seconds``, ``hang_write`` ({path, text, after}: a file written
under the working directory partway through a hang), ``connector_error`` (a dict, optional ``tool`` and
``text``: the run calls a connector tool that has vanished and ends on that error; see ``connector_drop_messages``),
``save_session`` (true: keep sessions the way Claude Code does, see ``session_file``).

Action mode (the launch argv carries an SDK-type ``--mcp-config``): after the initialize handshake the fake plays the
SDK MCP client over the control channel (``control_request`` subtype ``mcp_message``: JSON-RPC ``initialize``,
``notifications/initialized``, ``tools/list``), then issues each scripted ``tool_calls`` entry as ``tools/call`` on
``request_help`` and echoes the worker's reply as a tool result. Arguments come from the scenario only; the placeholder
``{first_ref}`` is replaced by the first ``message:<id>`` ref found in the prompt the fake was given, and
``{tool_results}`` in ``result_text`` by the JSON of the replies. Nothing here decides anything.
"""
import json
import os
import re
import sys
import time
import uuid

DEFAULT_AUTH = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max',
                'email': 'dervo-test@example.com'}
TOOL = 'mcp__dervo__request_help'


def cfg(name):
    return os.path.join(os.environ['CLAUDE_CONFIG_DIR'], name)


def record(name, obj):
    with open(cfg(name), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(obj) + '\n')


def emit(obj):
    sys.stdout.write(json.dumps(obj) + '\n')
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    record('received.jsonl', msg)
    return msg


class Client:
    """The SDK MCP client side: reads stdin in order, stashing user messages while waiting for a control response."""

    def __init__(self):
        self.users, self.counter = [], 0

    def await_response(self, request_id):
        while True:
            msg = read()
            if msg is None:
                return None
            if msg.get('type') == 'control_response' and (msg.get('response') or {}).get('request_id') == request_id:
                return msg
            if msg.get('type') == 'user':
                self.users.append(msg)

    def next_user(self):
        if self.users:
            return self.users.pop(0)
        while True:
            msg = read()
            if msg is None or msg.get('type') == 'user':
                return msg

    def mcp(self, method, params, rpc_id):
        self.counter += 1
        request_id = f'fake_req_{self.counter}'
        rpc = {'jsonrpc': '2.0', 'method': method}
        if rpc_id is not None:
            rpc['id'] = rpc_id
        if params is not None:
            rpc['params'] = params
        emit({'type': 'control_request', 'request_id': request_id, 'request': {'subtype': 'mcp_message', 'server_name': 'dervo', 'message': rpc}})
        return self.await_response(request_id)


def substitute(value, prompt, previous=None):
    """``previous`` is the text of the last tool result; when it parses as JSON, ``{prev.sha256}`` and ``{prev.content}``
    are replaced by its fields, so a scripted read can feed a scripted write (backend/evals seeds)."""
    if isinstance(value, str):
        match = re.search(r'message:msg_[0-9a-f]+', prompt)
        value = value.replace('{first_ref}', match.group(0) if match else 'message:none')
        if '{prev.' in value:
            try:
                fields = json.loads(previous or '')
            except ValueError:
                fields = {}
            for key in ('sha256', 'content'):
                if '{prev.%s}' % key in value:
                    value = value.replace('{prev.%s}' % key, str(fields.get(key, '')) if isinstance(fields, dict) else '')
        return value
    if isinstance(value, list):
        return [substitute(v, prompt, previous) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, prompt, previous) for k, v in value.items()}
    return value


DROPPED_TOOL = 'mcp__claude_ai_Wispr_Flow__search_notes'
DROPPED_TEXT = 'No such tool available: ' + DROPPED_TOOL


def connector_drop_messages(session_id, model, tool=DROPPED_TOOL, text=DROPPED_TEXT):
    """The ``connector_error`` scenario as the three stream-json messages the CLI would write, in order: the assistant
    calling the connector's tool, the tool result saying the tool is gone (``is_error``), and an error result. The
    wording is a guess until a watched run records the real one (notes/codex-briefs/run-reliability-watch.md, watch 2)."""
    tool_use_id = 'toolu_connector'
    return [
        {'type': 'assistant', 'session_id': session_id, 'parent_tool_use_id': None,
         'message': {'role': 'assistant', 'model': model, 'content': [{'type': 'text', 'text': 'searching your notes'},
                                                                     {'type': 'tool_use', 'id': tool_use_id, 'name': tool, 'input': {}}]}},
        {'type': 'user', 'session_id': session_id, 'parent_tool_use_id': None,
         'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tool_use_id, 'content': text, 'is_error': True}]}},
        {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'session_id': session_id, 'result': text,
         'usage': {'input_tokens': 10, 'output_tokens': 5}, 'modelUsage': {model: {'inputTokens': 10, 'outputTokens': 5}},
         'total_cost_usd': 0.0, 'permission_denials': []},
    ]


def session_file(session_id):
    """Where Claude Code keeps a session: $CLAUDE_CONFIG_DIR/projects/<working folder, each character that isn't a
    letter or digit made '-'>/<id>.jsonl. The folder name comes from the working folder as the OS reports it, so on
    Windows a short 8.3 path (C:\\Users\\RUNNER~1\\...) names a different folder than its long form."""
    return os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects', re.sub(r'[^A-Za-z0-9]', '-', os.getcwd()),
                        f'{session_id}.jsonl')


def main(argv):
    try:
        with open(cfg('fake-scenario.json'), encoding='utf-8') as fh:
            scenario = json.load(fh)
    except FileNotFoundError:
        scenario = {}
    record('calls.jsonl', {'argv': argv, 'cwd': os.getcwd(), 'env': dict(os.environ)})
    if 'auth' in argv:
        auth = scenario.get('auth') or DEFAULT_AUTH
        sys.stdout.write(json.dumps(auth))
        return 0 if auth.get('loggedIn') else 1
    model = argv[argv.index('--model') + 1]
    # A plain --resume (Continue on a paused run) carries no --session-id: Claude Code keeps the resumed session's id.
    session_id = next((a.split('=', 1)[1] for prefix in ('--session-id=', '--resume=') for a in argv if a.startswith(prefix)), None) or str(uuid.uuid4())
    # Neither flag: Claude Code makes up a fresh id, so the fake does too rather than writing None.jsonl.
    action = '--mcp-config' in argv and '"sdk"' in argv[argv.index('--mcp-config') + 1]
    first = read()
    if first is None or first.get('type') != 'control_request' or first['request'].get('subtype') != 'initialize':
        return 3
    emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': first['request_id'], 'response': {}}})
    client = Client()
    if action:
        if client.mcp('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'fake-cli', 'version': '0'}}, 0) is None:
            return 5
        client.mcp('notifications/initialized', None, None)
        client.mcp('tools/list', {}, 1)
    user = client.next_user()
    if user is None or user.get('type') != 'user':
        return 4
    prompt = user['message']['content'] if isinstance(user.get('message'), dict) else ''
    if scenario.get('save_session'):
        # --resume finds a session only in this working folder's own projects folder, as Claude Code does. Dervo
        # moves it there first (session_continuity.place_session); if it named the folder differently, this fails.
        resumed = next((a.split('=', 1)[1] for a in argv if a.startswith('--resume=')), None)
        if resumed and not os.path.isfile(session_file(resumed)):
            text = f'No conversation found with session ID: {resumed}'
            emit({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'session_id': session_id,
                  'result': text, 'usage': {'input_tokens': 0, 'output_tokens': 0}, 'modelUsage': {},
                  'total_cost_usd': 0.0, 'permission_denials': []})
            return 1
        os.makedirs(os.path.dirname(session_file(session_id)), exist_ok=True)
        with open(session_file(session_id), 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'type': 'user', 'sessionId': session_id, 'resumed': resumed}) + '\n')
    init = {'type': 'system', 'subtype': 'init', 'session_id': session_id, 'model': scenario.get('init_model', model), 'tools': [], 'mcp_servers': []}
    if action:
        init['tools'], init['mcp_servers'] = [TOOL], [{'name': 'dervo', 'status': 'connected'}]
    emit(init)
    tool_results = []
    previous = None
    for i, call in enumerate(scenario.get('tool_calls', []) if action else []):
        # ``name`` (default request_help) lets a seed script any Dervo tool the task offers (backend/evals); the host
        # still validates the call, so a tool outside the contract is refused as it would be for a real agent.
        name = call.get('name', 'request_help')
        arguments = substitute(call['arguments'], prompt, previous)
        tool_use_id = f'toolu_{i}'
        emit({'type': 'assistant', 'session_id': session_id, 'parent_tool_use_id': None,
              'message': {'role': 'assistant', 'model': model, 'content': [{'type': 'text', 'text': f'asking (call {i})'},
                                                                          {'type': 'tool_use', 'id': tool_use_id, 'name': f'mcp__dervo__{name}', 'input': arguments}]}})
        reply = client.mcp('tools/call', {'name': name, 'arguments': arguments}, 10 + i)
        if reply is None:
            return 6
        body = (reply.get('response') or {}).get('response') or {}
        result = (body.get('mcp_response') or {}).get('result') or {}
        content = result.get('content') or []
        text = content[0].get('text', '') if content else json.dumps(body)
        tool_results.append({'is_error': result.get('isError'), 'text': text})
        previous = text
        record('tool-results.jsonl', tool_results[-1])
        emit({'type': 'user', 'session_id': session_id, 'parent_tool_use_id': None,
              'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tool_use_id, 'content': text, 'is_error': bool(result.get('isError'))}]}})
    dropped = scenario.get('connector_error')
    if isinstance(dropped, dict):
        # A claude.ai connector that dropped mid-run: the tool it offered is called, the CLI answers that it's gone, and
        # the turn ends on that error. The same three messages the adapter test feeds through receive().
        for message in connector_drop_messages(session_id, model, **{k: dropped[k] for k in ('tool', 'text') if k in dropped}):
            emit(message)
        while sys.stdin.readline():
            pass
        return 1
    text = scenario.get('result_text', 'FAKE CLI RESULT: no reasoning was performed').replace('{tool_results}', json.dumps(tool_results))
    emit({'type': 'assistant', 'session_id': session_id, 'parent_tool_use_id': None,
          'message': {'role': 'assistant', 'model': model, 'content': [{'type': 'text', 'text': text}]}})
    if scenario.get('mode') == 'hang':
        # ``hang_write`` ({path, text, after}) writes a file under the working directory ``after`` seconds into the
        # hang: what a full-access CLI does when it keeps editing the project after its host died.
        total, write = float(scenario.get('hang_seconds', 30)), scenario.get('hang_write')
        if isinstance(write, dict):
            delay = min(max(float(write.get('after', 0)), 0.0), total)
            time.sleep(delay)
            with open(write['path'], 'w', encoding='utf-8') as fh:
                fh.write(write['text'])
            time.sleep(total - delay)
        else:
            time.sleep(total)
        return 0
    emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session_id, 'result': text,
          'usage': {'input_tokens': 10, 'output_tokens': 5}, 'modelUsage': {model: {'inputTokens': 10, 'outputTokens': 5}},
          'total_cost_usd': 0.0, 'permission_denials': []})
    while sys.stdin.readline():
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
