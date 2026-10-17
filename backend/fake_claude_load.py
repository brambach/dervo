"""FAKE Claude Code CLI for the parallel-run load test only (tools/load_parallel_runs.py, test_parallel_runs.py).

A real subprocess, so the host starts, reads, stops and reaps it the way it does Claude Code: the stream-json transport,
the SDK initialize handshake, the mcp_status question, then one task that streams for ``FAKE_LOAD_SECONDS`` and ends
with an assistant message and a result. It never calls a model and never touches the network. Every start is recorded
as one line in ``$FAKE_LOAD_LOG`` (session id, pid, time) so the test can count starts per task: a run that started
twice shows as two lines. SIGINT (Dervo's Stop) ends it at once with 130, as Claude Code does.
"""
import json
import os
import signal
import sys
import time


def emit(message):
    sys.stdout.write(json.dumps(message) + '\n')
    sys.stdout.flush()


def flag(argv, name):
    for index, value in enumerate(argv):
        if value == name and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith(name + '='):
            return value.split('=', 1)[1]
    return None


def busy(session, model, ends):
    """``FAKE_LOAD_BUSY``: stream the way a working Claude Code run does (tools/load_thread_scale.py). Text arrives as
    ``FAKE_LOAD_RATE`` deltas a second (default 30) in blocks, each block ends with its assistant message, and every
    ``FAKE_LOAD_TOOL_SECONDS`` (default 2) a web search starts and returns, which Dervo records as a step in any mode."""
    rate = float(os.environ.get('FAKE_LOAD_RATE', '30'))
    every = float(os.environ.get('FAKE_LOAD_TOOL_SECONDS', '2'))
    words = 'the run reads a file then edits it and checks the result before it moves on to the next step '.split()
    tools, block, next_tool = 0, [], time.monotonic() + every
    def event(body):
        emit({'type': 'stream_event', 'session_id': session, 'parent_tool_use_id': None, 'event': body})
    agents = subagents_start(session, model, int(os.environ.get('FAKE_LOAD_SUBAGENTS', '0')))
    next_progress = time.monotonic() + SUBAGENT_PROGRESS_EVERY
    event({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
    while time.monotonic() < ends:
        if agents and time.monotonic() >= next_progress:
            subagents_progress(session, agents)
            next_progress = time.monotonic() + SUBAGENT_PROGRESS_EVERY
        text = ' '.join(words[(len(block) * 5 + i) % len(words)] for i in range(5)) + ' '
        block.append(text)
        event({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}})
        if time.monotonic() >= next_tool:
            event({'type': 'content_block_stop', 'index': 0})
            emit({'type': 'assistant', 'session_id': session, 'parent_tool_use_id': None,
                  'message': {'role': 'assistant', 'model': model, 'content': [{'type': 'text', 'text': ''.join(block)}]}})
            tools += 1
            tool_id = f'toolu_load_{tools}'
            emit({'type': 'assistant', 'session_id': session, 'parent_tool_use_id': None,
                  'message': {'role': 'assistant', 'model': model, 'content': [
                      {'type': 'tool_use', 'id': tool_id, 'name': 'WebSearch', 'input': {'query': f'load test query {tools}'}}]}})
            emit({'type': 'user', 'session_id': session, 'parent_tool_use_id': None,
                  'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': tool_id, 'content': 'results'}]}})
            subagents_step(session, model, agents, tools, ''.join(block))
            block, next_tool = [], time.monotonic() + every
            event({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        time.sleep(min(1 / rate, max(0, ends - time.monotonic())))
    event({'type': 'content_block_stop', 'index': 0})
    subagents_finish(session, agents)


# ``FAKE_LOAD_SUBAGENTS`` (16.2): that many sub-agents run for the whole busy run, the way Claude Code reports them in
# stream-json (Agent SDK SDKTask*Message shapes; not recorded from a live run). Sub-agents stream no partial text, so
# each tool interval gives every one a complete message with its words and a Read step, and its result.
SUBAGENT_PROGRESS_EVERY = .5


def subagents_start(session, model, count):
    agents = [f'toolu_agent_{n}' for n in range(count)]
    if not agents:
        return agents
    emit({'type': 'assistant', 'session_id': session, 'parent_tool_use_id': None, 'message': {'role': 'assistant', 'model': model, 'content': [
        {'type': 'tool_use', 'id': agent, 'name': 'Agent', 'input': {'description': f'Look through part {n + 1}', 'prompt': 'Load test.',
                                                                     'subagent_type': 'general-purpose', 'run_in_background': True}}
        for n, agent in enumerate(agents)]}})
    for n, agent in enumerate(agents):
        emit({'type': 'system', 'subtype': 'task_started', 'session_id': session, 'task_id': f'task_{n}', 'tool_use_id': agent,
              'description': f'Look through part {n + 1}', 'subagent_type': 'general-purpose'})
    return agents


def subagents_step(session, model, agents, step, text):
    for agent in agents:
        read = f'{agent}_read_{step}'
        emit({'type': 'assistant', 'session_id': session, 'parent_tool_use_id': agent, 'message': {'role': 'assistant', 'model': model, 'content': [
            {'type': 'text', 'text': text}, {'type': 'tool_use', 'id': read, 'name': 'Read', 'input': {'file_path': 'README.md'}}]}})
        emit({'type': 'user', 'session_id': session, 'parent_tool_use_id': agent,
              'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': read, 'content': 'A load test project.\n' * 40}]}})


def subagents_progress(session, agents):
    for n, agent in enumerate(agents):
        emit({'type': 'system', 'subtype': 'task_progress', 'session_id': session, 'task_id': f'task_{n}', 'tool_use_id': agent,
              'description': f'Look through part {n + 1}', 'last_tool_name': 'Read',
              'usage': {'total_tokens': 1000, 'tool_uses': 1, 'duration_ms': 1000}})


def subagents_finish(session, agents):
    for n, agent in enumerate(agents):
        emit({'type': 'system', 'subtype': 'task_notification', 'session_id': session, 'task_id': f'task_{n}', 'tool_use_id': agent,
              'status': 'completed', 'output_file': '', 'summary': f'Part {n + 1} looked through.',
              'usage': {'total_tokens': 5000, 'tool_uses': 5, 'duration_ms': 5000}})
    if agents:
        emit({'type': 'user', 'session_id': session, 'parent_tool_use_id': None,
              'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': agent, 'content': 'Async agent launched.'} for agent in agents]}})


def main(argv):
    if len(argv) > 1 and argv[1] in ('--version', '-v'):
        print('2.1.280 (Claude Code)')
        return 0
    signal.signal(signal.SIGINT, lambda *_: os._exit(130))
    session = flag(argv, '--session-id') or flag(argv, '--resume')
    model = flag(argv, '--model')
    seconds = float(os.environ.get('FAKE_LOAD_SECONDS', '3'))
    log = os.environ.get('FAKE_LOAD_LOG')
    answered = False
    for line in sys.stdin:
        message = json.loads(line)
        kind = message.get('type')
        if kind == 'control_request' and message['request'].get('subtype') == 'initialize':
            emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                                           'response': {'commands': [], 'output_style': 'default'}}})
        elif kind == 'control_request' and message['request'].get('subtype') == 'mcp_status':
            emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                                           'response': {'mcpServers': []}}})
        elif kind == 'user' and not answered:
            answered = True
            if log:
                with open(log, 'a', encoding='utf-8') as out:
                    out.write(json.dumps({'session': session, 'pid': os.getpid(), 'at': time.time()}) + '\n')
            emit({'type': 'system', 'subtype': 'init', 'session_id': session, 'model': model, 'tools': [], 'mcp_servers': []})
            ends = time.monotonic() + seconds
            if os.environ.get('FAKE_LOAD_BUSY'):
                busy(session, model, ends)
            while time.monotonic() < ends:
                emit({'type': 'stream_event', 'session_id': session,
                      'event': {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '.'}}})
                time.sleep(min(.25, max(0, ends - time.monotonic())))
            emit({'type': 'assistant', 'session_id': session, 'parent_tool_use_id': None,
                  'message': {'role': 'assistant', 'model': model, 'content': [{'type': 'text', 'text': 'Load test answer.'}]}})
            emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session, 'result': 'Load test answer.',
                  'usage': {'input_tokens': 1, 'output_tokens': 1}, 'permission_denials': []})
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
