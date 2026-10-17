"""FAKE Codex app-server for backend tests only. Speaks the JSONL request/response/notification shapes the harness
worker expects; never infers, never touches credentials, network or shell.

It reads a scenario from ``$CODEX_HOME/fake-scenario.json`` (the worker passes an allowlisted environment, so the owned
home is the one channel a test controls), records every invocation in ``calls.jsonl`` and every stdin message in
``received.jsonl`` next to it. The session-flags layer and the effective configuration it reports are rebuilt from the
``-c key=value`` overrides it was actually launched with, so a test sees exactly the policy the worker sent.

Scenario keys: ``result_text``, ``mode`` (``ok`` or ``hang``), ``hang_seconds``, ``signed_out``, ``account``, ``catalog``,
``model`` (default catalog entry), ``config_patch`` (effective configuration overrides), ``ignore_interrupt``.
"""
import json
import os
import select
import sys
import time
import uuid

from codex_policy import VERSION

DEFAULT_MODEL = 'codex-fake-model-1'
DEFAULT_ACCOUNT = {'type': 'chatgpt', 'email': 'dervo-test@example.com', 'planType': 'plus'}


def home(name):
    return os.path.join(os.environ['CODEX_HOME'], name)


def record(name, obj):
    with open(home(name), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(obj) + '\n')


def emit(obj):
    sys.stdout.write(json.dumps(obj) + '\n')
    sys.stdout.flush()


def policy_from_argv(argv):
    """The inline-TOML ``-c key=value`` overrides back into a dict (keys are JSON strings, values JSON scalars or tables)."""
    policy, i = {}, 0
    while i < len(argv):
        if argv[i] == '-c' and i + 1 < len(argv):
            key, _, value = argv[i + 1].partition('=')
            policy[key] = json.loads(value.replace('"=', '":'))
            i += 2
        else:
            i += 1
    return policy


class Peer:
    def __init__(self, scenario, argv):
        self.s, self.policy = scenario, policy_from_argv(argv)
        self.thread_id, self.turn_id = str(uuid.uuid4()), 'fake-turn-1'
        self.mode = scenario.get('mode', 'ok')

    def handle(self, m):
        if 'method' not in m:
            return
        method, mid, p = m['method'], m.get('id'), m.get('params') or {}
        if method == 'initialize':
            emit({'id': mid, 'result': {'userAgent': f'dervo/{VERSION} (fake app-server)', 'codexHome': os.path.realpath(os.environ['CODEX_HOME'])}})
        elif method == 'initialized':
            return
        elif method == 'config/read':
            config = json.loads(json.dumps(self.policy))
            config.update(self.s.get('config_patch', {}))
            layers = [{'name': {'type': 'sessionFlags'}, 'version': 'v-flags', 'config': self.policy},
                      {'name': {'type': 'user', 'file': home('config.toml'), 'profile': None}, 'version': 'v-empty', 'config': {}},
                      {'name': {'type': 'system', 'file': '/etc/codex/config.toml'}, 'version': 'v-empty', 'config': {}}]
            emit({'id': mid, 'result': {'config': config, 'layers': layers}})
        elif method == 'account/read':
            emit({'id': mid, 'result': {'account': None if self.s.get('signed_out') else self.s.get('account', DEFAULT_ACCOUNT)}})
        elif method == 'model/list':
            emit({'id': mid, 'result': {'data': self.s.get('catalog', [{'model': self.s.get('model', DEFAULT_MODEL), 'hidden': False}]), 'nextCursor': None}})
        elif method == 'thread/start':
            emit({'id': mid, 'result': {'model': p.get('model'), 'cwd': p.get('cwd'), 'modelProvider': 'openai', 'approvalPolicy': 'on-request',
                                        'approvalsReviewer': 'user', 'sandbox': {'type': 'readOnly', 'networkAccess': False},
                                        'instructionSources': [], 'thread': {'id': self.thread_id, 'turns': []}}})
        elif method == 'turn/start':
            emit({'id': mid, 'result': {'turn': {'id': self.turn_id, 'status': 'inProgress'}}})
            self.stream()
        elif method == 'turn/interrupt':
            emit({'id': mid, 'result': {}})
            if not self.s.get('ignore_interrupt'):
                self.complete('interrupted')
        else:
            emit({'id': mid, 'error': {'code': -32601, 'message': f'fake does not know {method}'}})

    def stream(self):
        text = self.s.get('result_text', 'FAKE CODEX RESULT: no reasoning was performed')
        scope = {'threadId': self.thread_id, 'turnId': self.turn_id}
        emit({'method': 'turn/started', 'params': {'threadId': self.thread_id, 'turn': {'id': self.turn_id, 'status': 'inProgress'}}})
        for i in range(0, len(text), 7):
            emit({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'answer', 'delta': text[i:i + 7]}})
        emit({'method': 'item/completed', 'params': {**scope, 'item': {'type': 'agentMessage', 'id': 'answer', 'text': text}}})
        if self.mode != 'hang':
            self.complete('completed')

    def complete(self, status):
        emit({'method': 'turn/completed', 'params': {'threadId': self.thread_id, 'turn': {'id': self.turn_id, 'status': status}}})


def main(argv):
    try:
        with open(home('fake-scenario.json'), encoding='utf-8') as fh:
            scenario = json.load(fh)
    except FileNotFoundError:
        scenario = {}
    record('calls.jsonl', {'argv': argv, 'cwd': os.getcwd(), 'env': dict(os.environ)})
    peer = Peer(scenario, argv)
    deadline = time.time() + float(scenario['hang_seconds']) if 'hang_seconds' in scenario else None
    # Raw reads into our own buffer: readline on sys.stdin.buffer pulls two messages that arrive together (initialized
    # and config/read, sent back to back) into Python's buffer, and select then never wakes for the second.
    fd, pending = sys.stdin.fileno(), b''
    while True:
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            msg = json.loads(line)
            record('received.jsonl', msg)
            peer.handle(msg)
        wait = None if deadline is None else max(0.0, deadline - time.time())
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            return 0
        chunk = os.read(fd, 65536)
        if not chunk:
            return 0
        pending += chunk


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
