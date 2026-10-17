"""Installed-provider protocol adapters. Transport-independent, no live launch."""
from claude_policy import PLANS
from dervo_connections import Rejected
from pathlib import Path
import uuid
from codex_policy import VERSION


LIVE_BLOCKERS = {
    'claude': 'Live Claude connection is blocked: verify the chosen unmodified-tool or custom-agent integration contract, separate sign-ins and authenticated isolation first',
    'codex': 'Live Codex connection is blocked: sign into the owned CODEX_HOME, verify personal account/read metadata, configuration isolation and no-tool controls first',
}


def launch_live(binding):
    """Hard gate. Renderer flags and locally asserted identities can't enable it."""
    raise Rejected(LIVE_BLOCKERS.get(binding.get('provider'), 'Live provider is blocked: unsupported provider'))


from task_error import error_text  # shown to the person, so provider errors cross this boundary as text


class ClaudeAdapter:
    """Claude 2.1.263 print stream. Restricted to no-tool conversations."""
    def __init__(self, binding, emit):
        if binding['provider'] != 'claude':
            raise Rejected('Wrong provider adapter')
        self.binding, self.emit = dict(binding), emit
        self.state, self.result = 'new', None
        self.session_id = binding.get('session_id')

    def authenticate(self, metadata):
        from connections_ui import signed_in_as
        # This account's own sign-in, work or personal (connections_ui.signed_in_as). The project's Work or Personal
        # was already checked against it when the binding was read (dervo_connections.require_context).
        ok = signed_in_as(self.binding, metadata)
        if not ok:
            self.state = 'blocked'
            raise Rejected(f'{self.binding["category"].capitalize()} Claude subscription identity is missing, ambiguous or changed' if self.binding.get('method') != 'api_key'
                           else 'Claude Code is not using this account’s API key')
        self.state = 'ready'

    def command(self, executable):
        if self.state != 'ready' or not Path(executable).is_absolute():
            raise Rejected('Verify the account before preparing a provider command')
        argv = [executable, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
                '--safe-mode', '--restricted', '--setting-sources', '', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands',
                '--no-chrome', '--permission-mode', 'manual', '--permission-prompts', 'none',
                '--model', self.binding['model']]
        if self.session_id:
            try:
                parsed = uuid.UUID(self.session_id)
            except (ValueError, TypeError):
                raise Rejected('Saved Claude session ID is not a UUID') from None
            if str(parsed) != self.session_id:
                raise Rejected('Saved Claude session ID must be preserved exactly')
            argv += ['--resume', self.session_id]
        self.state = 'running'
        return argv

    def receive(self, message):
        if self.state not in ('running', 'stopping'):
            raise Rejected('Claude turn is not active')
        sid = message.get('session_id')
        if sid:
            if self.session_id and sid != self.session_id:
                raise Rejected('Claude changed the saved conversation ID')
            if not self.session_id:
                self.session_id = sid
                self.emit('session', {'session_id': sid})
        kind = message.get('type')
        if kind == 'system' and message.get('subtype') == 'init':
            if message.get('model') != self.binding['model']:
                raise Rejected('Claude effective model differs from selection')
        elif kind == 'stream_event':
            event = message.get('event', {})
            delta = event.get('delta', {})
            if event.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                self.emit('delta', {'item_id': 'answer', 'text': delta['text']})
        elif kind == 'result':
            if self.result is not None:
                raise Rejected('Duplicate Claude terminal result')
            self.result = message
            if 'result' in message:
                self.emit('snapshot', {'item_id': 'answer', 'text': message['result']})
            self.emit('usage', {'usage': message.get('usage', {}), 'modelUsage': message.get('modelUsage', {}),
                                'total_cost_usd': message.get('total_cost_usd')})
            for denial in message.get('permission_denials', []):
                self.emit('approval_denied', {'reason': 'Provider denied a tool request', 'tool': denial.get('tool_name')})
        elif kind == 'control_request':
            raise Rejected('Unexpected permission request in no-tool mode')

    def stop(self):
        if self.state != 'running':
            raise Rejected('No active Claude process')
        self.state = 'stopping'
        self.emit('stopping', {})
        return 'SIGINT'

    def exited(self, code):
        if self.state not in ('running', 'stopping'):
            raise Rejected('Claude exit was already handled')
        r = self.result
        if r is None:
            state, error = 'uncertain', 'Provider exited without a terminal result'
        elif (code == 0 and r.get('subtype') == 'success' and r.get('is_error') is False
              and r.get('terminal_reason') in (None, 'completed') and self.session_id):
            state, error = 'completed', None
        else:
            state, error = 'failed', error_text(r.get('result')) or 'Provider result or process failed'
        self.state = state
        self.emit('terminal', {'state': state, 'error': error})



class CodexAdapter:
    """Codex 0.156.1 stdio JSONL protocol. All tool approvals are declined."""
    sandbox_mode = 'read-only'
    sandbox_policy = {'type': 'readOnly', 'networkAccess': False}
    approval_policy = 'on-request'
    def __init__(self, binding, write, emit, *, recovery=False, catalog_only=False):
        self.catalog_only = catalog_only
        self.recovery = recovery
        if binding['provider'] != 'codex':
            raise Rejected('Wrong provider adapter')
        self.binding, self.write, self.emit = dict(binding), write, emit
        self.state, self.pending, self.sequence = 'new', {}, 0
        self.session_id, self.turn_id = binding.get('session_id'), None
        self.searches = {}  # webSearch item id -> 'started' or 'ended', so each search is one recorded step
        self.account_announced = False  # the one account/updated Codex sends while account/read is pending

    def request(self, method, params):
        self.sequence += 1
        self.pending[self.sequence] = method
        self.write({'id': self.sequence, 'method': method, 'params': params})

    def connect(self):
        if self.state != 'new':
            raise Rejected('Connection already initialized')
        self.state = 'initializing'
        self.request('initialize', {'clientInfo': {'name': 'dervo', 'title': 'Dervo', 'version': '0.1.0'},
                                    'capabilities': {'experimentalApi': False}})

    def _verify_config(self, result, login_method):
        from codex_policy import verify_config
        verify_config(result, login_method, cwd=self.binding['cwd'])

    def _thread_params(self):
        params = {'cwd': self.binding['cwd'], 'model': self.binding['model'], 'modelProvider': 'openai',
                  'sandbox': self.sandbox_mode, 'approvalPolicy': self.approval_policy, 'approvalsReviewer': 'user'}
        if self.session_id:
            params['threadId'] = self.session_id
        else:
            params['ephemeral'] = False
        return params

    def begin(self, text, client_id, *, images=()):
        if self.state != 'ready' or not text.strip() or not client_id:
            raise Rejected('Verify identity and conversation before sending')
        self.state = 'sending'
        params = {'threadId': self.session_id, 'input': [{'type': 'image', 'url': image} for image in images] + [{'type': 'text', 'text': text}],
                  'clientUserMessageId': client_id, 'cwd': self.binding['cwd'],
                  'model': self.binding['model'], 'approvalPolicy': self.approval_policy,
                  'approvalsReviewer': 'user', 'sandboxPolicy': self.sandbox_policy}
        if self.binding.get('effort') is not None:
            # Official TurnStartParams.effort (app-server protocol v2), verified against the catalog before this point.
            params['effort'] = self.binding['effort']
        self.request('turn/start', params)

    def effort_problem(self):
        """Why the requested effort cannot be sent, from the official ``model/list`` catalog (``supportedReasoningEfforts``
        of the selected model), or None. The catalog is the only authority; nothing is guessed or substituted."""
        effort = self.binding.get('effort')
        if effort is None:
            return None
        entry = next((m for m in self.models if m.get('model') == self.binding['model'] and m.get('hidden') is False), None)
        offered = [o.get('reasoningEffort') for o in ((entry or {}).get('supportedReasoningEfforts') or []) if isinstance(o, dict)]
        if type(effort) is not str or effort not in offered:
            return (f'The Codex model catalog does not advertise effort {effort!r} for {self.binding["model"]} '
                    f'(advertised: {", ".join(str(o) for o in offered) or "none"}). Nothing was sent.')
        return None

    def stop(self):
        if self.state != 'running' or not self.turn_id:
            raise Rejected('No acknowledged turn to interrupt')
        self.state = 'stopping'
        self.emit('stopping', {})
        self.request('turn/interrupt', {'threadId': self.session_id, 'turnId': self.turn_id})

    def steer(self, text):
        """Add a message to the running turn with app-server ``turn/steer``. A refusal comes back as ``steer_failed``."""
        if self.state != 'running' or not self.turn_id or type(text) is not str or not text.strip():
            raise Rejected('This run has finished, so the message waits for the next one.')
        self.request('turn/steer', {'threadId': self.session_id, 'expectedTurnId': self.turn_id,
                                    'input': [{'type': 'text', 'text': text}]})

    def _started(self, turn):
        if not turn.get('id') or (self.turn_id and self.turn_id != turn['id']):
            raise Rejected('Missing or changed provider turn ID')
        if self.turn_id:
            return
        if self.state != 'sending':
            raise Rejected('Unexpected provider turn')
        self.turn_id, self.state = turn['id'], 'running'
        self.emit('started', {'turn_id': self.turn_id})

    @staticmethod
    def search_summary(item):
        """What a Codex web search looked up, from its ``action``: the query or queries, or the host of a page it opened or
        searched in. Never the results. None while Codex hasn't said yet (the item's start carries no query)."""
        action = item.get('action') if isinstance(item.get('action'), dict) else {}
        def clean(value):
            return ' '.join(value.split())[:300] if isinstance(value, str) and value.strip() else None
        if action.get('type') in ('openPage', 'findInPage'):
            url = clean(action.get('url')) or ''
            return (url.split('/')[2] if url.count('/') >= 2 else url) or None
        queries = [q for q in (action.get('queries') or []) if isinstance(q, str) and q.strip()] if isinstance(action.get('queries'), list) else []
        return clean(action.get('query')) or clean(' · '.join(queries)) or clean(item.get('query'))

    def _search(self, item, phase):
        """Codex's own web search (``web_search`` is ``cached``, PLAN-native A5) as a step, in the same shape as Claude Code's
        WebSearch: started once, ended once, with what was searched."""
        item_id = item.get('id')
        if (type(item_id) is not str or not item_id or self.searches.get(item_id) == 'ended'
                or (item_id not in self.searches and len(self.searches) >= 500)):
            return
        summary = self.search_summary(item)
        if item_id not in self.searches:
            self.searches[item_id] = 'started'
            self.emit('native_tool', {'id': item_id[:128], 'name': 'WebSearch', 'phase': 'started', 'path': None,
                                      'summary': summary, 'prose': False})
        if phase == 'ended':
            self.searches[item_id] = 'ended'
            self.emit('native_tool', {'id': item_id[:128], 'name': 'WebSearch', 'phase': 'ended', 'is_error': False,
                                      'summary': summary})

    def _scope(self, params):
        if params.get('threadId') != self.session_id or params.get('turnId') != self.turn_id:
            raise Rejected('Provider event belongs to another conversation or turn')

    def receive(self, message):
        if not isinstance(message, dict):
            raise Rejected('Expected a protocol object')
        if 'method' in message and 'id' in message:
            method, params = message['method'], message.get('params', {})
            decisions = {'item/commandExecution/requestApproval': {'decision': 'decline'},
                         'item/fileChange/requestApproval': {'decision': 'decline'},
                         'item/permissions/requestApproval': {'permissions': {}, 'scope': 'turn'}}
            if method in decisions:
                self._scope(params)
                self.emit('approval_denied', {'request_id': message['id'], 'method': method,
                                              'reason': 'Granting tool permissions is not enabled'})
                self.write({'id': message['id'], 'result': decisions[method]})
            else:
                self.write({'id': message['id'], 'error': {'code': -32601, 'message': 'Dervo does not support this request'}})
            return
        if 'id' in message:
            method = self.pending.pop(message['id'], None)
            if method is None:
                raise Rejected('Unknown or duplicate response ID')
            if 'error' in message:
                if method == 'turn/interrupt':
                    # A failed Stop request says nothing about the turn outcome.
                    self.emit('error', {'error': error_text(message['error']), 'will_retry': False})
                    return
                if method == 'turn/steer':
                    # The turn couldn't take the message (it just ended, or it's a review): it waits for the next run.
                    self.emit('steer_failed', {'error': error_text(message['error'])})
                    return
                self.state = 'failed'
                self.emit('terminal', {'state': 'failed', 'error': error_text(message['error'])})
                return
            result = message.get('result', {})
            if method == 'turn/steer':
                self.emit('steer_read', {})
                return
            if method == 'initialize':
                if (not str(result.get('userAgent','')).startswith('dervo/'+VERSION+' ')
                        or result.get('codexHome') != self.binding['home']):
                    raise Rejected('Codex version or owned home differs from the verified protocol')
                self.write({'method': 'initialized', 'params': {}})
                self.state = 'configuring'
                self.request('config/read', {'includeLayers': True, 'cwd': self.binding['cwd']})
            elif method == 'config/read':
                # The login method this binding was launched with (every launch path uses codex_policy.login_method).
                from codex_policy import login_method
                self._verify_config(result, login_method(self.binding))
                self.state = 'authenticating'
                self.request('account/read', {'refreshToken': False})
            elif method == 'account/read':
                from connections_ui import signed_in_as
                ok = signed_in_as(self.binding, {'account': result.get('account') or {}})
                if not ok:
                    self.state = 'blocked'
                    raise Rejected(f'{self.binding["category"].capitalize()} subscription identity is missing, ambiguous or changed' if self.binding.get('method') != 'api_key'
                                   else 'Codex is not using this account’s API key')
                self.models, self.cursors = [], set()
                self.request('model/list', {'includeHidden': False, 'limit': 100})
            elif method == 'model/list':
                self.models.extend(result.get('data', []))
                cursor = result.get('nextCursor')
                if cursor:
                    if cursor in self.cursors or len(self.cursors) >= 20:
                        raise Rejected('Invalid model catalog pagination')
                    self.cursors.add(cursor)
                    self.request('model/list', {'includeHidden': False, 'limit': 100, 'cursor': cursor})
                elif self.catalog_only:
                    self.state = 'catalog'
                elif not any(m.get('model') == self.binding['model'] and m.get('hidden') is False for m in self.models):
                    self.state = 'blocked'
                elif self.effort_problem():
                    self.state = 'blocked'
                    raise Rejected(self.effort_problem())
                elif self.recovery:
                    if not self.session_id: raise Rejected('No saved provider thread to recover')
                    self.request('thread/read', {'threadId':self.session_id,'includeTurns':True})
                else:
                    self.request('thread/resume' if self.session_id else 'thread/start', self._thread_params())
            elif method == 'thread/read':
                self.history, self.state = result, 'history'
            elif method in ('thread/start', 'thread/resume'):
                expected = {'model': self.binding['model'], 'cwd': self.binding['cwd'], 'modelProvider': 'openai',
                            'approvalPolicy': self.approval_policy, 'approvalsReviewer': 'user',
                            'sandbox': self.sandbox_policy}
                if any(result.get(k) != v for k, v in expected.items()):
                    self.state = 'blocked'
                    raise Rejected('Provider effective settings differ from the selected settings')
                if result.get('instructionSources',[]) or any(t.get('status')=='inProgress' for t in result.get('thread',{}).get('turns',[])):
                    raise Rejected('Unexpected loaded instructions or active provider turn. No new text was sent.')
                sid = result.get('thread', {}).get('id')
                if not sid or (self.session_id and self.session_id != sid):
                    raise Rejected('Provider did not resume the saved conversation')
                self.session_id, self.state = sid, 'ready'
                self.emit('session', {'session_id': sid})
            elif method == 'turn/start':
                self._started(result.get('turn', {}))
            return
        method, params = message.get('method'), message.get('params', {})
        if method == 'turn/started':
            if params.get('threadId') != self.session_id:
                raise Rejected('Turn started in another conversation')
            self._started(params.get('turn', {}))
        elif method == 'item/agentMessage/delta':
            self._scope(params)
            if self.state not in ('running', 'stopping'):
                raise Rejected('Late provider delta')
            self.emit('delta', {'item_id': params['itemId'], 'text': params['delta']})
        elif method == 'item/started':
            item = params.get('item', {})
            if isinstance(item, dict) and item.get('type') == 'webSearch':
                self._scope(params)
                self._search(item, 'started')
        elif method == 'item/completed':
            self._scope(params)
            item = params.get('item', {})
            if item.get('type') == 'agentMessage':
                self.emit('snapshot', {'item_id': item['id'], 'text': item['text']})
            elif item.get('type') == 'webSearch':
                self._search(item, 'ended')
        elif method == 'turn/completed':
            turn = params.get('turn', {})
            self._scope({'threadId': params.get('threadId'), 'turnId': turn.get('id')})
            if turn.get('status') not in ('completed', 'interrupted', 'failed'):
                raise Rejected('Unknown terminal status')
            self.state = turn['status']
            import provider_failure
            self.emit('terminal', {'state': self.state, 'error': error_text(turn.get('error')) or None,
                                   'provider_failure': provider_failure.codex(turn.get('error')) if self.state == 'failed' else None})
        elif method == 'error':
            self._scope(params)
            self.emit('error', {'error': error_text(params.get('error')), 'will_retry': params.get('willRetry', False)})
        elif method == 'account/updated':
            # Codex 0.156.1 loads the saved sign-in when account/read asks for it and announces it with one
            # account/updated just before the reply (measured 25 Sep on a signed-in profile; 0.154.0 sent none). The reply
            # that follows is the account Dervo verifies, so that one announcement passes when it names this account's
            # login method. Any other account/updated still stops the conversation.
            expected = 'apikey' if self.binding.get('method') == 'api_key' else 'chatgpt'
            if (self.state != 'authenticating' or 'account/read' not in self.pending.values()
                    or self.account_announced or params.get('authMode') != expected):
                self.state = 'blocked'
                raise Rejected('Account changed. Stop and verify identity again')
            self.account_announced = True
        elif method == 'thread/tokenUsage/updated':
            # Codex reports the thread's running token totals; only this conversation's thread counts.
            if params.get('threadId') == self.session_id and isinstance(params.get('tokenUsage'), dict):
                self.emit('usage', {'tokenUsage': params['tokenUsage']})
        elif method == 'account/rateLimits/updated':
            if isinstance(params.get('rateLimits'), dict):
                self.emit('rate_limits', params['rateLimits'])
