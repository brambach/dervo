"""Private host bridge to the personal OpenRouter Cline loop.

This child never receives a key or performs provider/file operations. The host
must authorize and dispatch each returned request through its existing owners.
No renderer operation or Claude/Codex selection enters this module.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import time

from codex_coding import dynamic_tools, validate_arguments
from dervo_connections import Rejected
from openrouter_routes import model_id
from release_profiles import require_provider
from transport import JsonLines, OwnedProcess

LIMIT = 4 * 1024 * 1024
STOPPED = 'The OpenRouter loop stopped. Check the task and any pending charge before continuing.'


class _Process(OwnedProcess):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decoder = JsonLines(limit=LIMIT)

    def send(self, message):
        raw = (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
        if len(raw) > LIMIT:
            raise Rejected('This OpenRouter task exceeds the message limit.')
        self._send_bytes(raw)


class Loop:
    def __init__(self, *, resources, node, task, authorize, runtime_root=None):
        # Check the build before process, source or profile access.
        self.resources = Path(resources)
        require_provider('openrouter', self.resources)
        if not callable(authorize):
            raise Rejected('A current task permission check is required.')
        self.authorize = authorize
        self._allowed()
        self.task = deepcopy(task)
        model_id(self.task.get('model'))
        self.tools = {tool['name']: tool for tool in self.task['tools']}
        # Only the established Dervo host contracts are accepted. A caller can't
        # smuggle another executable or a provider-specific tool into the loop.
        allowed = {tool['name']: {key: tool[key] for key in ('name', 'description', 'inputSchema')}
                   for tool in dynamic_tools(self.tools)}
        if self.tools != allowed or len(self.tools) != len(self.task['tools']):
            raise Rejected('The OpenRouter task has an unsupported tool contract.')
        root = Path(runtime_root) if runtime_root is not None else self.resources / 'backend' / 'openrouter_runtime'
        if not root.is_absolute() or root.is_symlink() or not (root / 'bridge.mjs').is_file():
            raise Rejected('The personal OpenRouter runtime is missing.')
        executable = Path(node)
        if not executable.is_absolute() or not executable.is_file():
            raise Rejected('The selected Node runtime is missing.')
        self.closed = False
        self.ready = False
        self.pending = None
        self.request_id = 0
        self.event_seq = 0
        self.deadline = time.monotonic() + 1800
        # Node's permission model blocks file access outside these dependencies,
        # writes, workers and subprocesses. Seatbelt also denies network access.
        policy = '(version 1)(allow default)(deny network*)(deny file-write*)'
        self.process = _Process(['/usr/bin/sandbox-exec', '-p', policy, str(executable),
                                 '--permission', '--allow-fs-read=' + str(root.resolve()), str(root / 'bridge.mjs')],
                                cwd=str(root), env={'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8'},
                                byte_limit=32 * 1024 * 1024)

    def _allowed(self):
        require_provider('openrouter', self.resources)
        if self.authorize() is not True:
            raise Rejected('This OpenRouter task no longer has permission to run.')

    def _send(self, message):
        if self.closed:
            raise Rejected(STOPPED)
        try:
            self._allowed()
            self.process.send(message)
        except Exception:
            self.close()
            raise Rejected(STOPPED) from None

    def poll(self):
        if self.closed:
            return []
        try:
            self._allowed()
            if time.monotonic() >= self.deadline:
                raise Rejected(STOPPED)
            try:
                messages = self.process.read(.01)
            except TimeoutError:
                return []
            if messages is None:
                raise Rejected(STOPPED)
            events = []
            for message in messages:
                if not self.ready:
                    if message != {'kind': 'ready', 'protocol': 1, 'engine': '@cline/agents', 'version': '0.0.83'}:
                        raise Rejected(STOPPED)
                    self.ready = True
                    self._send({'op': 'start', 'task': self.task})
                    continue
                kind = message.get('kind')
                if message.get('runId') != self.task['runId']:
                    raise Rejected(STOPPED)
                if kind in ('model', 'tool'):
                    if (set(message) != {'kind', 'id', 'runId', 'payload'} or self.pending is not None
                            or type(message['id']) is not int or message['id'] != self.request_id + 1):
                        raise Rejected(STOPPED)
                    self._payload(kind, message['payload'])
                    self.request_id = message['id']
                    self.pending = (kind, message['id'])
                elif kind in ('delta', 'terminal'):
                    if type(message.get('seq')) is not int or message['seq'] != self.event_seq + 1:
                        raise Rejected(STOPPED)
                    self.event_seq = message['seq']
                    if kind == 'delta':
                        if (set(message) != {'kind', 'runId', 'seq', 'iteration', 'text'}
                                or type(message['text']) is not str or type(message['iteration']) is not int
                                or not 1 <= message['iteration'] <= self.task['maxModelCalls']):
                            raise Rejected(STOPPED)
                    else:
                        if (set(message) != {'kind', 'runId', 'seq', 'state', 'modelCalls'}
                                or message['state'] not in ('completed', 'stopped')
                                or type(message['modelCalls']) is not int
                                or not 0 <= message['modelCalls'] <= self.task['maxModelCalls']
                                or (message['state'] == 'completed' and self.pending is not None)):
                            raise Rejected(STOPPED)
                        self.close()
                else:
                    raise Rejected(STOPPED)
                events.append(message)
                if self.closed:
                    # No actionable request from this batch may escape once its
                    # child has stopped, including a request before terminal.
                    return [event for event in events if event['kind'] not in ('model', 'tool')]
            return events
        except Exception:
            self.close()
            raise Rejected(STOPPED) from None

    def _payload(self, kind, payload):
        if type(payload) is not dict:
            raise Rejected(STOPPED)
        if kind == 'tool':
            if (set(payload) != {'name', 'arguments', 'toolCallId'} or payload['name'] not in self.tools
                    or type(payload['toolCallId']) is not str
                    or not re.fullmatch('[A-Za-z0-9_-]{1,100}', payload['toolCallId'])):
                raise Rejected(STOPPED)
            validate_arguments(payload['name'], payload['arguments'])
        elif (set(payload) != {'model', 'systemPrompt', 'messages', 'tools'}
              or payload['model'] != self.task['model'] or payload['systemPrompt'] != self.task['systemPrompt']
              or payload['tools'] != self.task['tools'] or type(payload['messages']) is not list):
            raise Rejected(STOPPED)

    def _response(self, kind, request_id, op, **data):
        if self.pending != (kind, request_id):
            self.close()
            raise Rejected('The OpenRouter response belongs to another operation.')
        self._send(dict(op=op, runId=self.task['runId'], id=request_id, **data))

    def model_event(self, request_id, event):
        self._response('model', request_id, 'event', event=event)

    def model_end(self, request_id):
        self._response('model', request_id, 'end')
        self.pending = None

    def tool_result(self, request_id, *, success, text):
        if type(success) is not bool or type(text) is not str or len(text.encode()) > 131072:
            self.close()
            raise Rejected('The tool result is invalid or too large.')
        self._response('tool', request_id, 'result', result={'success': success, 'text': text})
        self.pending = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.process.close()
