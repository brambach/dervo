"""Claude Code coding protocol. Only trusted host code may construct this.

Same host-broker contract as codex_coding: the provider never touches files or
tests itself. It calls dervo_read_file, dervo_write_file and dervo_run_tests
through an SDK-style MCP server that this adapter answers over the CLI's
stream-json control channel. The coordinator executes each tool, records
evidence and returns the real result. Nothing here fabricates success.

Sequencing follows the Claude Agent SDK: launch, send the initialize control
request, wait for its control_response, then write the user message. See
claude_policy for the grounding notes.
"""
from claude_policy import PLANS
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import time
from types import MappingProxyType
import uuid
from codex_coding import TOOL_KEYS, TOOL_CALL_CEILING, ToolRequest, TrustedTaskBinding, validate_arguments, ToolPathRejected, recheck_approval, tool_call_problem
from claude_policy import NATIVE_TOOLS, SEARCH_TOOLS, SERVER, SUBAGENT_HOOKS, SUBAGENT_TOOL, WORKTREE_HOOK, argv, auth_status, check_uuid, connectors_argv, executable, full_access_argv, mcp_tools, subagent_environment, subagent_hook, user_message
from dervo_connections import Rejected
import platform_os
from mcp_connectors import server_key
from transport import OwnedProcess, clean_environment

TERMINAL = ('completed', 'interrupted', 'failed', 'uncertain')
ACTIVE = ('initializing', 'initialized', 'running', 'stopping')
# Claude Code's task events (Agent SDK SDKTaskStarted/Progress/Notification/UpdatedMessage) for sub-agents in full access.
SUBAGENT_EVENTS = ('task_started', 'task_progress', 'task_notification', 'task_updated')
SUBAGENT_PROGRESS_SECONDS = 2  # a sub-agent's progress is passed on at most this often, since each one is a record save
LIMIT_SUBAGENTS = 100  # sub-agents recorded per run; later ones still run and their steps still count, unrecorded as calls


def subagent_usage(usage):
    """A sub-agent's running totals from a task event: tokens, tool calls and milliseconds. Only whole numbers pass."""
    usage = usage if isinstance(usage, dict) else {}
    return {name: usage[key] for key, name in (('total_tokens', 'tokens'), ('tool_uses', 'tool_uses'), ('duration_ms', 'duration_ms'))
            if type(usage.get(key)) is int and usage[key] >= 0}


# How a tool result reads when the tool it names is no longer offered (a claude.ai connector dropped mid-run, #9). A guess
# from Claude Code's usual wording until a watched run records the exact text (run-reliability-watch.md, watch 2).
TOOL_VANISHED = ('no such tool', 'unknown tool', 'tool not found', 'is not available')
CONNECTOR_DROPPED = ('The {connector} connector dropped during this run, so the agent couldn’t finish. '
                     'Reconnect it in Claude Code and send the request again.')


def tool_vanished(content):
    """Whether a tool result's text (a string, or the CLI's list of text blocks) says the tool isn't offered any more."""
    parts = [content] if isinstance(content, str) else [b.get('text') for b in content if type(b) is dict] if isinstance(content, list) else []
    text = ' '.join(p for p in parts if isinstance(p, str)).lower()
    return any(marker in text for marker in TOOL_VANISHED)


def _string(value, limit, *, empty=False, field='protocol string'):
    if type(value) is not str or (not empty and not value) or len(value.encode('utf-8')) > limit:
        raise Rejected(f'{field}: expected a string within {limit} UTF-8 bytes'
                       + ('' if empty else ' (nonempty)'))


def validate_tool(tool, arguments):
    """Same shape rules as the Codex adapter (one implementation, codex_coding.validate_arguments)."""
    validate_arguments(tool, arguments)


def effective_model_problem(result, model, assistant_models=frozenset()):
    """Returns (problem, auxiliary_models).

    Official semantics (code.claude.com): result modelUsage is the per-model session
    breakdown and includes background functionality, which runs on the Haiku default
    (ANTHROPIC_DEFAULT_HAIKU_MODEL "also used for background functionality"). Every
    assistant message names its model, so the primary model is verified per message.
    A modelUsage entry is accepted as auxiliary only when no assistant message used
    it. The selected model must appear whenever a genuine assistant message was seen;
    a run the primary model never spoke in has nothing to verify here, and exited()
    refuses to call it completed. Nothing is whitelisted by name.
    """
    usage = result.get('modelUsage', result.get('model_usage'))
    if usage is None:
        return None, []
    if type(usage) is not dict or any(type(key) is not str for key in usage):
        return 'Provider reported malformed model usage', []
    if usage and assistant_models and model not in usage:
        return f'The selected model {model} does not appear in the provider usage report', []
    others = sorted(set(usage) - {model})
    drifted = [name for name in others if name in assistant_models]
    if drifted:
        return f'Assistant model {", ".join(drifted)} differs from the selected model {model}', []
    return None, others


# The conversation events: before the task these mean a turn ran that Dervo didn't start (system only with subtype init).
TURN_EVENTS = ('system', 'stream_event', 'assistant', 'user', 'result')


def line_kind(kind, subtype):
    """A stream-json line's type and subtype, kept to plain identifier characters so they're safe to show and log."""
    def plain(value):
        return re.sub(r'[^A-Za-z0-9_]', '', value)[:40] if isinstance(value, str) else ''
    return plain(kind) or 'untyped', plain(subtype) or None


class StartedEarly(Rejected):
    """Claude Code wrote a conversation event (TURN_EVENTS) before Dervo sent the task."""
    def __init__(self, kind, subtype=None):
        self.kind, self.subtype = line_kind(kind, subtype)
        super().__init__(f'Claude Code started a turn before Dervo sent the task ({" ".join(filter(None, [self.kind, self.subtype]))})')


class ClaudeCodingAdapter:
    """States: new, ready, launched, initializing, initialized, running, stopping, then a TERMINAL state or blocked."""

    def __init__(self, binding, write, emit, *, task):
        if binding.get('provider') != 'claude':
            raise Rejected('Wrong provider adapter')
        self.replays_expected, self.steers_unread = 0, 0
        if not isinstance(task, TrustedTaskBinding) or dict(task.provider_binding) != binding:
            raise Rejected('Coding provider binding differs from the approved task')
        if set(binding) & {'executable', 'argv', 'env', 'protocol', 'factory'}:
            raise Rejected('Caller launch overrides are not accepted')
        self.binding, self.write, self.emit, self.task = dict(binding), write, emit, task
        self.session_id = binding.get('session_id')
        self.resume = self.session_id is not None
        if self.resume:
            check_uuid(self.session_id)
        # A new message continuing the conversation's previous session (session_continuity). Dropped before launch when
        # that session's file isn't saved any more; the task then starts fresh and says so.
        self.fork_from = binding.get('fork_from')
        if self.fork_from is not None and (self.resume or check_uuid(self.fork_from) is None):
            raise Rejected('A continued session starts a new session ID')
        self.state, self.turn_id, self.result, self.init_response = 'new', None, None, None
        self.stop_requested, self.started = False, False
        self.assistant_models, self.auxiliary_models = set(), []
        self.api_error = None  # Claude Code's own report of a failed API call, in its words
        self.limit_rejected = False  # a rate-limit event in this run said the account is out of allowance (status rejected)
        self._counter, self._init_request_id = 0, None
        self._tools, self._seen_calls, self._seen_requests = {}, set(), set()
        self._answer_text, self._block_text, self._last_text = '', '', ''
        self._text_open = False
        self.shots = None  # full access: the per-run folder Claude Code may save screenshots in and read them from
        self.connectors = ()  # full access: your MCP server and claude.ai connector names, allowed by name (mcp_connectors)
        self.hidden_connectors = ()  # full access: the ones this project doesn't allow, denied so they're never offered
        self.servers_file = None  # the allowed ones from your own MCP configuration, as an --mcp-config file (mcp_connectors)
        self._native = {}  # Claude Code's own tool calls (web search in normal mode): tool_use id -> name, for results and repeats
        self._connectors = {}  # full access: tool_use id -> connector name, so a vanished tool's result names its connector
        self.connector_dropped = None  # the connector whose tool vanished mid-run (#9, failure A), for the terminal error
        self._subagents = {}  # full access: Agent tool_use id -> its task_id once started, for task events and its steps
        self._subagent_progress = {}  # Agent tool_use id -> monotonic time of the last progress event passed on
        # dd #8, 23 Sep: in print mode Claude Code stops waiting for MCP servers after a short deadline and connects the
        # rest in the background, so a first turn could start with its connectors still "pending". Between the handshake
        # and the task, the host asks Claude Code's mcp_status until none of the allowed connectors is pending, or
        # CONNECTOR_WAIT_SECONDS pass. None: not waiting (normal mode, or no connectors). A float: the wait's deadline.
        self.connecting_until = None
        self.connector_status = None  # the last answer: {'pending': [...], 'failed': [...]}
        self._mcp_request_id, self._mcp_next_ask = None, 0.0
        self._own_requests = set()  # control requests Dervo sent, so an echo of one is never taken for Claude Code's own
        self._skipped = set()  # (type, subtype) of lines skipped before the task, each logged once (_skipped_early)
        self._approved()

    def _public_text(self):
        separator = '\n\n' if self._answer_text and self._block_text else ''
        return (self._answer_text + separator + self._block_text)[:65536]

    def _finish_text(self):
        if self._block_text:
            self._answer_text = self._public_text()
            self._last_text = self._block_text
        self._block_text, self._text_open = '', False

    def _text_snapshot(self):
        self.emit('snapshot', {'item_id': 'answer', 'text': self._public_text()})

    def _stream_text(self, event):
        kind = event.get('type')
        if kind in ('message_start', 'message_stop', 'content_block_start', 'content_block_stop'):
            self._finish_text()
            if kind == 'content_block_start':
                block = event.get('content_block') or {}
                self._text_open = block.get('type') == 'text'
        elif kind == 'content_block_delta':
            delta = event.get('delta') or {}
            if delta.get('type') == 'text_delta':
                text = delta.get('text')
                _string(text, 65536, empty=True)
                separator = '\n\n' if self._answer_text and not self._block_text and text else ''
                self._block_text = (self._block_text + text)[:65536]
                self._text_open = True
                self.emit('delta', {'item_id': 'answer', 'text': separator + text})

    def _assistant_text(self, body):
        # Claude emits one complete assistant event per content block, before
        # content_block_stop. Several blocks can share the same message ID.
        # Reconcile only the open text block, never the earlier answer.
        reconciled = False
        for block in body.get('content', []):
            if type(block) is not dict or block.get('type') != 'text':
                continue
            if type(block.get('text')) is not str:
                raise Rejected('Malformed assistant text block')
            if reconciled:
                self._finish_text()
            streamed = self._text_open
            self._block_text = block['text'][:65536]
            self._text_snapshot()
            reconciled = True
            if not streamed:
                self._finish_text()

    def _native_summary(self, name, arguments):
        """A short, output-free description of one of Claude Code's own tool calls: the command, the project-relative file,
        the pattern or the web host. Never the tool's output."""
        arguments = arguments if isinstance(arguments, dict) else {}
        def text(key):
            value = arguments.get(key)
            return ' '.join(value.split())[:300] if isinstance(value, str) else None
        if name == 'Bash':
            return None, text('description') or text('command')
        if name in ('Read', 'Edit', 'Write'):
            path = text('file_path')
            cwd = self.binding.get('cwd', '').rstrip('/')
            if path and cwd and path.startswith(cwd + '/'):
                path = path[len(cwd) + 1:]
            elif path and self.shots and path.startswith(str(self.shots).rstrip('/') + '/'):
                path = 'screenshot ' + path.rsplit('/', 1)[-1]
            return path, None
        if name in ('Glob', 'Grep'):
            return text('path'), text('pattern')
        if name == 'WebFetch':
            url = text('url') or ''
            return None, url.split('/')[2] if url.count('/') >= 2 else url
        if name == 'WebSearch':
            return None, text('query')  # what was searched: it left the Mac, so the thread says so
        return None, None

    @staticmethod
    def connector_of(name):
        """A tool from one of your MCP servers or claude.ai connectors (``mcp__<server>__<tool>``, any server but Dervo's), as
        the connector's name and the tool's, for Progress: ("Wispr Flow", "search notes"). None for anything else."""
        if type(name) is not str or not name.startswith('mcp__') or name.startswith(f'mcp__{SERVER}__'):
            return None
        server, _, tool = name[5:].partition('__')
        if not server or not tool:
            return None
        server = server.removeprefix('claude_ai_')  # how Claude Code names a connector it fetched from claude.ai
        def words(text):
            return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())[:80]
        return (words(server) or None), (words(tool) or None)

    def _native_uses(self, body, parent=None):
        # Normal mode offers Claude Code's web search and nothing else of its own, so only searches are recorded there.
        # ``parent`` is the Agent call a sub-agent's step belongs to: the step is recorded like the run's own, naming it.
        full = self.task.full_access
        recorded = NATIVE_TOOLS if full else SEARCH_TOOLS
        under = {'parent': parent[:128]} if parent is not None else {}
        for block in body.get('content') or []:
            if type(block) is not dict or block.get('type') != 'tool_use':
                continue
            name, tool_id = block.get('name'), block.get('id')
            if full and name == SUBAGENT_TOOL:
                self._subagent_call(tool_id, block.get('input'), parent)
                continue
            connector = self.connector_of(name) if full or self.task.connectors_only else None
            if (name not in recorded and connector is None) or type(tool_id) is not str or tool_id in self._native or len(self._native) >= 500:
                continue
            if connector is not None:
                # A connector call: which connector and which tool, never its arguments (a message's text, an event's
                # guests) or its result.
                self._native[tool_id] = 'Connector'
                self._connectors[tool_id] = connector[0]
                self.emit('native_tool', {'id': tool_id[:128], 'name': 'Connector', 'phase': 'started', 'path': None,
                                          'summary': connector[1], 'connector': connector[0], 'prose': False, **under})
                continue
            self._native[tool_id] = name
            path, summary = self._native_summary(name, block.get('input'))
            described = name == 'Bash' and isinstance((block.get('input') or {}).get('description'), str) and bool(block['input']['description'].strip())
            self.emit('native_tool', {'id': tool_id[:128], 'name': name, 'phase': 'started', 'path': path, 'summary': summary, 'prose': described, **under})

    def _native_results(self, body):
        content = body.get('content') if isinstance(body, dict) else None
        for block in content if isinstance(content, list) else []:
            if type(block) is dict and block.get('type') == 'tool_result' and block.get('tool_use_id') in self._subagents:
                # The Agent call returned: a sub-agent in the foreground has finished, one in the background has only
                # been launched and reports through task_notification later. Never its text.
                self.emit('subagent', {'id': block['tool_use_id'][:128], 'phase': 'returned', 'is_error': block.get('is_error') is True})
                continue
            if type(block) is dict and block.get('type') == 'tool_result' and block.get('tool_use_id') in self._native:
                tool_id, is_error = block['tool_use_id'], block.get('is_error') is True
                step = {'id': tool_id[:128], 'name': self._native[tool_id], 'phase': 'ended', 'is_error': is_error}
                if is_error and tool_id in self._connectors and tool_vanished(block.get('content')):
                    # The connector's tool was offered at the start and is gone now: the connector dropped mid-run
                    # (#9, failure A). Recorded on the step; the run is left to end as the CLI ends it, with no retry.
                    self.connector_dropped = self._connectors[tool_id] or 'claude.ai'
                    step.update(connector_dropped=True, connector=self.connector_dropped)
                self.emit('native_tool', step)

    def _subagent_call(self, tool_id, arguments, parent):
        """The run, or one of its sub-agents, handed work to a sub-agent. Recorded: its short description, its kind,
        whether it asked for the background or a worktree, and the call it sits under. Never its prompt."""
        if type(tool_id) is not str or tool_id in self._subagents:
            return
        self._subagents[tool_id] = None  # known from here on, so its messages are its own and not refused
        if len(self._subagents) > LIMIT_SUBAGENTS:
            return
        arguments = arguments if isinstance(arguments, dict) else {}
        def text(key, limit):
            value = arguments.get(key)
            return ' '.join(value.split())[:limit] or None if isinstance(value, str) else None
        background = arguments.get('run_in_background')
        isolation = arguments.get('isolation')
        self.emit('subagent', {'id': tool_id[:128], 'phase': 'called', 'description': text('description', 300),
                               'kind': text('subagent_type', 80), 'background': background if type(background) is bool else None,
                               'isolation': isolation if isolation in ('worktree', 'remote') else None,
                               'parent': parent[:128] if type(parent) is str else None})

    def _subagent_event(self, message):
        """Claude Code's task events for the sub-agents this run called (Agent SDK SDKTask*Message). Task events for
        anything else, such as a command left running in the background, carry no host action here."""
        if message.get('skip_transcript') is True:
            return
        subtype, task_id, tool_id = message['subtype'], message.get('task_id'), message.get('tool_use_id')
        if type(task_id) is not str or not task_id:
            return
        if type(tool_id) is not str:
            tool_id = next((call for call, task in self._subagents.items() if task == task_id), None)
        if tool_id not in self._subagents or list(self._subagents).index(tool_id) >= LIMIT_SUBAGENTS:
            return
        event = {'id': tool_id[:128]}
        if subtype == 'task_started':
            self._subagents[tool_id] = task_id
            description = message.get('description')
            event.update(phase='started', description=' '.join(description.split())[:300] or None if isinstance(description, str) else None)
        elif subtype == 'task_progress':
            # Each event passed on is a record save, so at most one per sub-agent per SUBAGENT_PROGRESS_SECONDS.
            now = time.monotonic()
            if now - self._subagent_progress.get(tool_id, -SUBAGENT_PROGRESS_SECONDS) < SUBAGENT_PROGRESS_SECONDS:
                return
            self._subagent_progress[tool_id] = now
            last = message.get('last_tool_name')
            event.update(phase='progress', last_tool=last[:80] if isinstance(last, str) and last else None, **subagent_usage(message.get('usage')))
        elif subtype == 'task_notification':
            status, summary = message.get('status'), message.get('summary')
            event.update(phase='finished', status=status if status in ('completed', 'failed', 'stopped') else None,
                         summary=summary.strip()[:600] or None if isinstance(summary, str) else None, **subagent_usage(message.get('usage')))
        elif subtype == 'task_updated':
            patch = message.get('patch') if isinstance(message.get('patch'), dict) else {}
            if patch.get('status') not in ('failed', 'killed'):
                return
            event.update(phase='finished', status='failed' if patch['status'] == 'failed' else 'stopped', summary=None)
        self.emit('subagent', event)

    def _subagent_message(self, parent, body):
        """A sub-agent's own message. Its model must be the run's, like every message of the run's own (no silent
        fallback). Its tool calls become steps under its Agent call; its words never join the reply."""
        if not self.task.full_access or parent not in self._subagents:
            raise Rejected('Subagent output is not permitted in a Dervo coding task')
        model = body.get('model')
        if model == '<synthetic>':
            return  # Claude Code's own report of a sub-agent's failed API call; the Agent call's result carries it
        if model != self.binding['model']:
            raise Rejected(f'A sub-agent used {model} instead of the selected model {self.binding["model"]}')
        self._native_uses(body, parent=parent)

    def _api_error(self, body):
        parts = [block['text'] for block in body.get('content') or [] if type(block) is dict
                 and block.get('type') == 'text' and type(block.get('text')) is str and block['text'].strip()]
        text = '\n'.join(parts).encode('utf-8')[:4096].decode('utf-8', 'ignore').strip()
        self.api_error = text or 'Claude Code reported an API error without details'
        self._finish_text()  # keep whatever the model had streamed before the call failed

    def _result_text(self, text):
        text = text[:65536]
        # A result normally repeats the last block. Older/non-streaming peers
        # can provide only a result or finish an unframed partial text block.
        if self._block_text and text.startswith(self._block_text):
            self._block_text = text
        self._finish_text()
        if text and text not in (self._last_text, self._answer_text):
            self._block_text = text
            self._finish_text()
        self._text_snapshot()

    def _approved(self, request=None):
        try:
            if self.task.authorize(self.task, request) is not True:
                raise Rejected('Task scope is no longer approved')
        except Exception:
            self.state = 'blocked'
            self.cancel_pending_tools()
            raise

    def authenticate(self, metadata):
        """Official auth-status metadata only. This account's own subscription with the exact identity, on a plan its
        Work or Personal side accepts, or, for an API-key account, Claude Code reporting API-key auth from ANTHROPIC_API_KEY."""
        self._approved()
        if self.state != 'new':
            raise Rejected('Claude coding adapter was already authenticated')
        from connections_ui import signed_in_as
        # This account's own sign-in, work or personal; the binding already matched it to the project's Work or Personal.
        ok = type(metadata) is dict and signed_in_as(self.binding, metadata)
        if not ok:
            self.state = 'blocked'
            raise Rejected(f'{str(self.binding.get("category", "")).capitalize()} Claude subscription identity is missing, ambiguous or changed' if self.binding.get('method') != 'api_key'
                           else 'Claude Code is not using this account’s API key')
        self.state = 'ready'

    def command(self, executable_path):
        self._approved()
        if self.state != 'ready':
            raise Rejected('Verify the account before preparing a provider command')
        if not self.session_id:
            # Dervo chooses the session ID so it can be persisted before any content is sent.
            self.session_id = str(uuid.uuid4())
        self.turn_id = str(uuid.uuid4())
        if self.task.full_access:
            if self.binding.get('method') == 'api_key':
                raise Rejected('Full access isn’t available on an API-key account. The key is in that run’s own environment, '
                               'where any command it runs could print it.')
            command = full_access_argv(executable_path, self.binding['model'], self.session_id, resume=self.resume, tools=self.task.tools,
                                       effort=self.binding.get('effort'), shots=self.shots, fork_from=self.fork_from,
                                       connectors=self.connectors, hidden=self.hidden_connectors, computer_access=self.task.computer_access,
                                       commands=self.task.commands, servers_file=self.servers_file)
        elif self.task.connectors_only and self.connectors:
            # With no connector allowed by name (the list failed, ran out of time or is empty) it's the normal restricted
            # launch, so --strict-mcp-config keeps every server out rather than loading them with no rule (security S1).
            command = connectors_argv(executable_path, self.binding['model'], self.session_id, resume=self.resume, tools=self.task.tools,
                                      effort=self.binding.get('effort'), fork_from=self.fork_from, connectors=self.connectors, hidden=self.hidden_connectors,
                                      servers_file=self.servers_file)
        else:
            command = argv(executable_path, self.binding['model'], self.session_id, resume=self.resume, tools=self.task.tools,
                           effort=self.binding.get('effort'), fork_from=self.fork_from)
        self.state = 'launched'
        return command

    def initialize(self):
        """SDK handshake. No user content may be written until the response arrives."""
        self._approved()
        if self.state != 'launched':
            raise Rejected('Initialize exactly once after launch')
        self._counter += 1
        self._init_request_id = f'req_{self._counter}_{uuid.uuid4().hex[:8]}'
        self._own_requests.add(self._init_request_id)
        self.state = 'initializing'
        self.write({'type': 'control_request', 'request_id': self._init_request_id,
                    'request': {'subtype': 'initialize', 'hooks': SUBAGENT_HOOKS if self.task.full_access else None}})

    @property
    def connecting(self):
        """True while the first message waits for connectors to finish connecting."""
        return self.connecting_until is not None

    def wait_for_connectors(self, now=None):
        """Called once the handshake succeeds. Only a run with connectors allowed by name waits: full access, or a Home job
        with connectors and no shell (beta.15)."""
        if (self.task.full_access or self.task.connectors_only) and self.connectors:
            self.connecting_until = (time.monotonic() if now is None else now) + CONNECTOR_WAIT_SECONDS
            self.emit('connectors', {'state': 'connecting', 'pending': list(self.connectors), 'failed': []})

    def poll_connectors(self, now=None):
        """One step of the wait: ask Claude Code which servers are still connecting, at most every CONNECTOR_POLL_SECONDS
        and one question at a time, and stop waiting at the deadline. The first message goes when this returns False."""
        if self.connecting_until is None:
            return False
        now = time.monotonic() if now is None else now
        if now >= self.connecting_until:
            pending = (self.connector_status or {}).get('pending', list(self.connectors))
            self._connected('slow', pending, (self.connector_status or {}).get('failed', []))
            return False
        if self._mcp_request_id is None and now >= self._mcp_next_ask:
            self._counter += 1
            self._mcp_request_id = f'req_{self._counter}_{uuid.uuid4().hex[:8]}'
            self._own_requests.add(self._mcp_request_id)
            self.write({'type': 'control_request', 'request_id': self._mcp_request_id, 'request': {'subtype': 'mcp_status'}})
        return True

    def _mcp_status(self, response, now=None):
        self._mcp_request_id = None
        self._mcp_next_ask = (time.monotonic() if now is None else now) + CONNECTOR_POLL_SECONDS
        if self.connecting_until is None:
            return
        if response.get('subtype') != 'success':
            self._connected('unknown', [], [])  # an older Claude Code without mcp_status: nothing to wait for
            return
        servers = (response.get('response') or {}).get('mcpServers')
        if not isinstance(servers, list):
            self._connected('unknown', [], [])
            return
        allowed = {server_key(name) for name in self.connectors}
        pending, failed = [], []
        for server in servers:
            if not isinstance(server, dict) or not isinstance(server.get('name'), str) or server_key(server['name']) not in allowed:
                continue
            if server.get('status') == 'pending':
                pending.append(server['name'][:100])
            elif server.get('status') in ('failed', 'needs-auth'):
                failed.append(server['name'][:100])
        self.connector_status = {'pending': pending, 'failed': failed}
        if not pending:
            self._connected('ready', [], failed)

    def _connected(self, state, pending, failed):
        self.connecting_until = None
        self.emit('connectors', {'state': state, 'pending': list(pending)[:LIMIT_NAMES], 'failed': list(failed)[:LIMIT_NAMES]})

    def begin(self, text, client_id=None):
        self._approved()
        if self.state != 'initialized' or type(text) is not str or not text.strip():
            raise Rejected('Verify identity, session and initialization before sending')
        self.state = 'running'
        self.replays_expected = 1
        self.write(user_message(text, images=self.task.images() if self.task.images else ()))

    def steer(self, text, images=()):
        """Add a message to the running turn. Claude Code takes it in at its next step and carries on (checked live
        against CLI 2.1.274, 17 Sep): --replay-user-messages echoes it back when it's read. ``images`` are host-loaded
        PNG data URLs, the same envelope ``begin`` sends; the conversation path doesn't pass them yet (see
        coding_integration.steer, another brief's file), so a message with an image still waits in the queue."""
        self._approved()
        if self.state != 'running' or self.result is not None or type(text) is not str or not text.strip():
            raise Rejected('This run has finished, so the message waits for the next one.')
        self.steers_unread += 1
        self.write(user_message(text, images=images))

    @property
    def pending_tool_requests(self):
        requests = tuple(self._tools.values())
        for request in requests:
            self._approved(request)
        return requests

    def receive(self, message):
        if self.state in ('blocked',) + TERMINAL:
            raise Rejected('Coding adapter is no longer active')
        if self.state not in ACTIVE:
            raise Rejected('Claude turn is not active')
        try:
            if type(message) is not dict:
                raise Rejected('Expected a protocol object')
            sid = message.get('session_id')
            if sid is not None and sid != '' and sid != self.session_id:
                raise Rejected('Claude changed the saved conversation ID')
            kind = message.get('type')
            if kind == 'control_request':
                self._control(message)
            elif kind == 'control_response':
                self._control_result(message)
            elif kind == 'control_cancel_request':
                cancelled = [t for t, r in self._tools.items() if r.request_id[0] == message.get('request_id')]
                for token in cancelled:
                    del self._tools[token]
                if cancelled:
                    self.emit('tools_cancelled', {'tokens': cancelled})
            elif self.state in ('initializing', 'initialized'):
                # Before the task only conversation events are refused (beta.18, notes/STOPPING-explore-20261005.md): the
                # guard proves no turn ran before Dervo's task (PLAN-native A1). Everything else is Claude Code's own
                # bookkeeping and is skipped, including kinds a newer CLI adds: 2.1.284 writes system/commands_changed
                # after the handshake answer and as connectors connect, plus keep_alive, command_lifecycle,
                # prompt_suggestion and peer_message_hold. None of them starts a turn.
                if kind in TURN_EVENTS and (kind != 'system' or message.get('subtype') == 'init'):
                    raise StartedEarly(kind, message.get('subtype'))
                if kind == 'rate_limit_event':
                    self._rate_limit(message)
                else:
                    self._skipped_early(kind, message.get('subtype'))
            elif kind == 'system' and message.get('subtype') == 'init':
                if message.get('model') != self.binding['model']:
                    raise Rejected('Claude effective model differs from selection')
                if sid != self.session_id:
                    raise Rejected('Claude did not use the persisted session ID')
                if not self.started:
                    self.started = True
                    self.emit('started', {'turn_id': self.turn_id})
            elif kind == 'stream_event':
                if message.get('parent_tool_use_id') is not None:
                    if not self.task.full_access:
                        raise Rejected('Subagent output is not permitted in a Dervo coding task')
                    return  # a sub-agent's words are its own, never the reply's; its result comes back as the Agent call's
                self._stream_text(message.get('event') or {})
            elif kind == 'system' and message.get('subtype') in SUBAGENT_EVENTS and self.task.full_access:
                self._subagent_event(message)
            elif kind == 'assistant':
                body = message.get('message')
                if type(body) is not dict:
                    raise Rejected('Malformed assistant message')
                if message.get('parent_tool_use_id') is not None:
                    self._subagent_message(message['parent_tool_use_id'], body)
                    return
                model = body.get('model')
                if model == '<synthetic>' or message.get('isApiErrorMessage') is True:
                    # Claude Code writes its own assistant message when an API call fails
                    # ("API Error: Overloaded"). It is a report, not model drift, and not
                    # the model's words: it never joins assistant_models or the answer.
                    self._api_error(body)
                elif model != self.binding['model']:
                    raise Rejected(f'Assistant message used {model} instead of the selected model {self.binding["model"]}')
                else:
                    self.assistant_models.add(model)
                    self.api_error = None  # the turn carried on after the error report
                    self.limit_rejected = False  # and after a rejected rate-limit event (extra usage, another model)
                    self._assistant_text(body)
                    self._native_uses(body)
            elif kind == 'result':
                if self.result is not None:
                    raise Rejected('Duplicate Claude terminal result')
                self.result = message
                if isinstance(message.get('result'), str) and not (self.api_error and message.get('is_error')):
                    # An error result after Claude Code's own API-error report repeats that report; it
                    # belongs in task.error, not in the model's answer.
                    self._result_text(message['result'])
                problem, self.auxiliary_models = effective_model_problem(message, self.binding['model'], self.assistant_models)
                self.emit('usage', {'usage': message.get('usage', {}),
                                    'modelUsage': message.get('modelUsage', message.get('model_usage', {})),
                                    'total_cost_usd': message.get('total_cost_usd'),
                                    'assistant_models': sorted(self.assistant_models),
                                    'auxiliary_models': list(self.auxiliary_models),
                                    'model_problem': problem})
                for denial in message.get('permission_denials') or []:
                    self.emit('approval_denied', {'reason': 'Provider denied a tool request',
                                                  'tool': denial.get('tool_name') if isinstance(denial, dict) else None})
            elif kind == 'rate_limit_event':
                self._rate_limit(message)
            elif kind == 'user' and message.get('isReplay') is True:
                # Claude Code read one of our own messages. The first is the task; later ones are messages added mid-run.
                if self.replays_expected:
                    self.replays_expected -= 1
                elif self.steers_unread:
                    self.steers_unread -= 1
                    if self.result is not None:
                        self.result = None  # it arrived after the turn ended, so Claude Code starts another turn for it
                    self.emit('steer_read', {})
            elif kind == 'user' and self.task.full_access:
                parent = message.get('parent_tool_use_id')
                if parent is not None and parent not in self._subagents:
                    raise Rejected('Subagent output is not permitted in a Dervo coding task')
                self._native_results(message.get('message'))
            elif kind == 'user' and message.get('parent_tool_use_id') is None:
                self._native_results(message.get('message'))  # normal mode: only a web search's result is matched
            # assistant, user echoes and other informational events carry no host action.
        except Exception:
            self.state = 'blocked'
            self.cancel_pending_tools()
            raise

    def _skipped_early(self, kind, subtype):
        """One host-errors.log line per kind of line skipped before the task, so a keep-alive can't fill the log."""
        seen = line_kind(kind, subtype)
        if seen in self._skipped:
            return
        self._skipped.add(seen)
        import sys
        print(f'Claude Code wrote type={seen[0]} subtype={seen[1] or "-"} before Dervo sent the task; skipped, it starts no turn.',
              file=sys.stderr)

    def _rate_limit(self, message):
        import provider_usage
        info = message.get('rate_limit_info')
        if isinstance(info, dict) and info.get('status') in ('rejected', 'allowed', 'allowed_warning'):
            # The newest event says whether the account is out right now: a later allowed one clears it, and a
            # rejected window the run carries on past on extra usage doesn't count (reviews, 23 Sep).
            self.limit_rejected = info['status'] == 'rejected' and info.get('isUsingOverage') is not True
        limits = provider_usage.claude_rate_limits(info)
        if limits:
            self.emit('rate_limits', limits)

    def _control_result(self, message):
        response = message.get('response')
        if type(response) is not dict:
            raise Rejected('Malformed control response')
        if self._mcp_request_id is not None and response.get('request_id') == self._mcp_request_id:
            self._mcp_status(response)
            return
        if response.get('request_id') != self._init_request_id:
            # --replay-user-messages echoes everything Dervo writes, including the replies it sends to Claude Code's own
            # requests (checked live on CLI 2.1.274, 18 Sep). An echo of our own answer is not a message to act on.
            return
        if self.state != 'initializing':
            raise Rejected('Unexpected control response')
        if response.get('subtype') == 'success':
            self.init_response = response.get('response') or {}
            self.state = 'initialized'
            if self.servers_file is not None:
                # Claude Code has read your servers by now (checked on 2.1.295: they still connect with the file gone),
                # so their settings don't sit on disk for the whole run, or after a host that crashed (review 9 Oct, 5).
                try:
                    os.remove(self.servers_file)
                except OSError:
                    pass
            self.wait_for_connectors()
            return
        raise Rejected(f'Claude rejected initialization: {response.get("error", "unknown error")}')

    def _control_response(self, request_id, payload):
        self.write({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': request_id,
                                                              'response': payload}})

    def _mcp_response(self, request_id, message_id, *, result=None, error=None):
        body = {'jsonrpc': '2.0'}
        if message_id is not None:
            body['id'] = message_id
        body['error' if error is not None else 'result'] = error if error is not None else result
        self._control_response(request_id, {'mcp_response': body})

    def _control(self, message):
        request_id, request = message.get('request_id'), message.get('request')
        _string(request_id, 256, field='control_request.request_id')
        if request_id in self._own_requests:
            return  # an echo of Dervo's own handshake or connector question, not Claude Code asking anything
        if type(request) is not dict:
            raise Rejected('Malformed control request')
        subtype = request.get('subtype')
        if subtype == 'can_use_tool':
            self.emit('approval_denied', {'request_id': request_id, 'tool': request.get('tool_name'),
                                          'reason': 'Direct tool permissions are not granted. Tools run through the Dervo host.'})
            self._control_response(request_id, {'behavior': 'deny',
                                                'message': 'Dervo brokers tools through its host. Use the dervo tools.'})
            return
        if subtype == 'hook_callback' and request.get('callback_id') == WORKTREE_HOOK and self.task.full_access:
            hook_input = request.get('input')
            output, refused = subagent_hook(hook_input)
            tool_id = request.get('tool_use_id') or (hook_input.get('tool_use_id') if isinstance(hook_input, dict) else None)
            if refused and type(tool_id) is str and tool_id:
                self._subagent_call(tool_id, hook_input.get('tool_input'), None)  # usually known already, from the assistant message
                if tool_id in self._subagents:
                    self.emit('subagent', {'id': tool_id[:128], 'phase': 'refused'})
            self._control_response(request_id, output)
            return
        if subtype != 'mcp_message':
            self.write({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': request_id,
                                                                  'error': 'Dervo does not support this request'}})
            return
        if request.get('server_name') != SERVER:
            raise Rejected('Claude addressed an unknown MCP server')
        rpc = request.get('message')
        if type(rpc) is not dict:
            raise Rejected('Malformed MCP message')
        method, message_id, params = rpc.get('method'), rpc.get('id'), rpc.get('params') or {}
        if method == 'initialize':
            self._mcp_response(request_id, message_id, result={
                'protocolVersion': params.get('protocolVersion') or '2024-11-05',
                'capabilities': {'tools': {}}, 'serverInfo': {'name': SERVER, 'version': '0.1.0'}})
        elif message_id is None:
            # Notifications get no JSON-RPC reply, but the control request still expects an ack.
            self._mcp_response(request_id, None, result={})
        elif method == 'ping':
            self._mcp_response(request_id, message_id, result={})
        elif method == 'tools/list':
            self._mcp_response(request_id, message_id, result={'tools': mcp_tools(self.task.tools)})
        elif method == 'tools/call':
            self._tool_request(request_id, message_id, params)
        else:
            self._mcp_response(request_id, message_id, error={'code': -32601, 'message': 'Dervo does not support this method'})

    def _tool_request(self, request_id, message_id, params):
        if self.state != 'running':
            raise Rejected('Coding tool request has no active turn')
        if type(message_id) is int:
            if not -(2 ** 53) < message_id < 2 ** 53:
                raise Rejected('Tool request ID is outside the safe integer range')
        else:
            _string(message_id, 256)
        call_id = f'{request_id}:{message_id}'
        if call_id in self._seen_calls or request_id in self._seen_requests:
            raise Rejected('Replayed tool call')
        if len(self._seen_calls) >= TOOL_CALL_CEILING:
            raise Rejected('The agent kept calling tools after Dervo asked it to finish')
        # Anything the model got wrong gets an error result it can read and correct. Only protocol problems above end the run.
        if type(params) is not dict or not {'name', 'arguments'} <= set(params) or set(params) - {'name', 'arguments', '_meta'}:
            problem = 'A tool call needs exactly the fields name and arguments.'
        else:
            problem = tool_call_problem(params['name'], params['arguments'], self.task.tools, len(self._seen_calls), len(self._tools))
        if problem is not None:
            self._approved()
            self._seen_calls.add(call_id)
            self._seen_requests.add(request_id)
            self._mcp_response(request_id, message_id, result={
                'content': [{'type': 'text', 'text': problem}], 'isError': True})
            return
        request = ToolRequest(str(uuid.uuid4()), self.task.task_id, (request_id, message_id), self.session_id,
                              self.turn_id, call_id, params['name'], MappingProxyType(deepcopy(params['arguments'])))
        self._approved(request)
        self._seen_calls.add(call_id)
        self._seen_requests.add(request_id)
        self._tools[request.token] = request

    def complete_tool_result(self, token, *, success, text):
        """Host-thread only. Return actual broker output, never invented success."""
        if type(token) is not str or token not in self._tools or self.state != 'running':
            raise Rejected('Tool completion is unknown, cancelled or stale')
        if type(success) is not bool:
            raise Rejected('Tool success must be a boolean')
        _string(text, 65536, empty=True)
        request = self._tools[token]
        self._approved(request)
        request_id, message_id = request.request_id
        body = {'jsonrpc': '2.0', 'id': message_id,
                'result': {'content': [{'type': 'text', 'text': text}], 'isError': not success}}
        if len(json.dumps({'mcp_response': body}).encode()) > 120000:
            raise Rejected('Encoded tool result exceeds the transport budget')
        del self._tools[token]
        self._control_response(request_id, {'mcp_response': body})

    def cancel_pending_tools(self):
        """Invalidates tokens without a false success reply. Parent cancels jobs."""
        tokens = list(self._tools)
        self._tools.clear()
        if tokens:
            self.emit('tools_cancelled', {'tokens': tokens})

    def stop(self):
        if self.state != 'running':
            raise Rejected('No active Claude process')
        self.stop_requested = True
        self.cancel_pending_tools()
        self.state = 'stopping'
        self.emit('stopping', {})
        return 'SIGINT'

    def exited(self, code):
        """Process exit is the only terminal confirmation. Maps to completed, interrupted, failed or uncertain.

        A synthetic API-error message is Claude Code's own report that the turn ended on a
        failed call. Every file effect is host-side and recorded before its tool result is
        delivered, and tool calls only come from genuine assistant messages, so nothing is
        in doubt after that report: the run is failed and retryable, never uncertain, even
        when the process exits without a result line. A vanish with no report stays
        uncertain because the host cannot say why it ended.
        """
        if self.state not in ('launched',) + ACTIVE:
            raise Rejected('Claude exit was already handled')
        self.cancel_pending_tools()
        r = self.result
        model_problem = None
        if r is not None:
            model_problem, self.auxiliary_models = effective_model_problem(r, self.binding['model'], self.assistant_models)
        result_text = r.get('result') if r is not None and isinstance(r.get('result'), str) else None
        from_api = False
        if r is None:
            if self.stop_requested:
                state, error = 'interrupted', 'Stopped. The provider exited without a terminal result.'
            elif self.api_error:
                state, error, from_api = 'failed', self.api_error, True
            else:
                state, error = 'uncertain', f'Provider exited without a terminal result (exit status {code})'
        elif model_problem:
            state, error = 'failed', model_problem
        elif (code == 0 and r.get('subtype') == 'success' and r.get('is_error') is False
              and r.get('terminal_reason') in (None, 'completed') and self.started
              and self.assistant_models and self.api_error is None):
            state, error = 'completed', None
        elif self.stop_requested:
            state, error = 'interrupted', result_text or 'Stopped before completion'
        elif self.api_error:
            state, error, from_api = 'failed', self.api_error, True
        elif result_text and (r.get('is_error') or r.get('subtype') != 'success'):
            state, error = 'failed', result_text
            # A limit that arrives only as the error result is still the provider's side (systems report, problem 2).
            # Read from its words alone: a rejected event earlier in the run doesn't make an ordinary failure a limit.
            import provider_failure
            from_api = provider_failure.claude(result_text) == 'usage_limit'
        elif not self.assistant_models:
            state, error = 'failed', f'The provider ended the turn without an assistant message from {self.binding["model"]}'
        else:
            state, error = 'failed', result_text or 'Provider result or process failed'
        if state == 'failed' and self.connector_dropped and not model_problem:
            # A failed run after a connector's tool vanished: the reason is the drop, said plainly instead of the CLI's
            # own error text. The state rules above are unchanged; a dropped connector is a failed run.
            error = CONNECTOR_DROPPED.format(connector=self.connector_dropped)
        self.state = state
        # auxiliary_models records extra usage honestly; the coordinator keeps state and error.
        self.emit('terminal', {'state': state, 'error': error, 'auxiliary_models': list(self.auxiliary_models),
                               'assistant_models': sorted(self.assistant_models),
                               'provider_failure': self.failure_kind(error) if from_api else None})

    def failure_kind(self, text):
        """'usage_limit' or 'api_error' for a run that ended on Claude Code's own report (provider_failure.claude)."""
        import provider_failure
        return provider_failure.claude(text, rejected=self.limit_rejected)


def full_access_path(base):
    """Common toolchain folders that exist on this Mac, ahead of the fixed system PATH. Nothing from a shell profile.

    Windows (b16/windows milestone 4): installers put Node, Python and the rest on the account's PATH in the registry,
    not in fixed folders, so it's the PATH a new PowerShell window gets (platform_os.account_environment, the same one
    the project terminal uses), after the usual per-user folders and before ``base``. Only PATH: none of the account's
    other variables. When Windows can't give it, ``base`` alone, as before."""
    import os
    home = os.path.expanduser('~')
    # Node and Python from a version manager (9 Oct, MCP parity): an MCP server started with npx, node or uvx failed to
    # start under Dervo when the only Node was nvm's, Volta's or mise's. Their fixed shim folders and nvm's default go
    # first, as in a terminal that loads them, so the Node you picked wins over Homebrew's.
    nvm = nvm_default(home)
    extra = ([nvm] if nvm else []) + [f'{home}/.volta/bin', f'{home}/.local/share/mise/shims', f'{home}/.asdf/shims',
              f'{home}/.nodenv/shims', f'{home}/.pyenv/shims', f'{home}/.local/share/fnm/aliases/default/bin']
    extra += [f'{home}/.local/bin', f'{home}/.cargo/bin', f'{home}/.bun/bin', '/opt/homebrew/bin', '/opt/homebrew/sbin',
              '/Library/Frameworks/Python.framework/Versions/Current/bin']
    if platform_os.WINDOWS:
        home = platform_os.account_home()
        extra = [os.path.join(home, '.local', 'bin'), os.path.join(home, '.cargo', 'bin'), os.path.join(home, '.bun', 'bin')]
        try:
            account = {name.upper(): value for name, value in platform_os.account_environment().items()}.get('PATH', '')
        except OSError:
            account = ''
        folders = {}
        for folder in [p for p in extra if os.path.isdir(p)] + account.split(os.pathsep) + base.split(os.pathsep):
            # Windows paths aren't case sensitive, so C:\Windows and C:\WINDOWS\ are one folder. The first one stays.
            if folder.strip():
                folders.setdefault(os.path.normcase(folder.strip().rstrip('\\/')), folder.strip())
        return os.pathsep.join(folders.values())
    return os.pathsep.join([p for p in extra if os.path.isdir(p)] + [base])


def nvm_default(home):
    """The bin folder of nvm's default Node (~/.nvm/alias/default, as `nvm use default` reads it), or None. An alias may
    name a version, a major ("22"), or another alias (lts/iron, followed through ~/.nvm/alias); one that resolves to
    nothing installed gets the newest installed. "system" means no nvm Node. Nothing from a shell profile is run."""
    import os
    versions = os.path.join(home, '.nvm', 'versions', 'node')
    try:
        installed = [name for name in os.listdir(versions) if name.startswith('v') and os.path.isdir(os.path.join(versions, name, 'bin'))]
    except OSError:
        return None
    def number(name):
        try:
            return tuple(int(part) for part in name[1:].split('.'))
        except ValueError:
            return ()
    def read(name):
        path = os.path.normpath(os.path.join(home, '.nvm', 'alias', name))
        if not path.startswith(os.path.join(home, '.nvm', 'alias') + os.sep):
            return ''
        try:
            with open(path, encoding='utf-8') as file:
                return file.read(64).strip()
        except (OSError, UnicodeError):
            return ''
    alias = read('default')
    for _ in range(5):  # an alias naming an alias (default -> lts/iron -> v20.18.0), never a loop
        if not alias or alias.startswith('v') or alias[:1].isdigit() or alias in ('node', 'stable', 'system'):
            break
        alias = read(alias) or alias
    if alias == 'system':
        return None
    if alias and not alias.startswith('v') and alias[:1].isdigit():
        alias = 'v' + alias
    matching = [name for name in installed if name == alias] or [name for name in installed if alias.startswith('v') and name.startswith(alias + '.')]
    best = max(matching or installed, key=number, default=None)
    return os.path.join(versions, best, 'bin') if best else None


# Beta.17.1: the defaults. Each project picks its own (run_limits), and a run takes them from its TrustedTaskBinding.
import run_limits as _run_limits
IDLE_SECONDS = _run_limits.DEFAULTS['idle_seconds']  # no output from Claude Code for this long ends the run
RUN_SECONDS = _run_limits.DEFAULTS['run_seconds']    # one run never lasts longer than this, however busy (None: no cap)
AUTH_SECONDS = 5  # auth status at the start of a run: with --version's 2 seconds, well under the bridge's 10 per request
STOP_SECONDS = 10  # after Stop, how long Claude Code gets to exit on its own before Dervo ends it
CONNECTOR_WAIT_SECONDS = 15  # dd #8: how long a first message waits for connectors still connecting
CONNECTOR_POLL_SECONDS = .5
# Beta.15: how long a first run waits for the account's connector list before launching. Listing takes about 9 seconds
# and gives up at mcp_connectors.LIST_TIMEOUT (30); this is the backstop if it never reports back.
CONNECTOR_LIST_WAIT_SECONDS = 35
LIMIT_NAMES = 64
def stop_text(limit, seconds=None, who='Claude Code'):
    """The plain line for a run Dervo ended at one of its own limits: ``idle`` (silence), ``hard`` (run length) or
    ``stop`` (the CLI didn't exit after your Stop)."""
    if limit == 'idle':
        return f'{who} sent nothing for {_run_limits.duration(seconds or IDLE_SECONDS)}, so Dervo stopped the run.'
    if limit == 'hard':
        return f'The run went on for {_run_limits.duration(seconds or RUN_SECONDS)}, this project’s limit, so Dervo stopped it.'
    return f'{who} didn’t exit within {STOP_SECONDS} seconds of Stop, so Dervo ended it.'


HOST_STOPS = {'idle': stop_text('idle'), 'hard': stop_text('hard'), 'stop': stop_text('stop')}
# Up to beta.17 the limits were fixed and so were these lines. Older records still carry them.
OLD_HOST_STOPS = ('Claude Code sent nothing for 30 minutes, so Dervo stopped the run.',
                  'The run reached Dervo’s 6-hour limit, so Dervo stopped it.')


class HostStop(Rejected):
    """Dervo ended the run at one of its own limits. ``limit`` is idle, hard or stop."""
    def __init__(self, limit, text):
        super().__init__(text)
        self.limit = limit


GIT_GUARD_HOOKS = ('pre-commit', 'pre-merge-commit', 'pre-push', 'pre-rebase', 'pre-applypatch')
# Beta.18: git runs reference-transaction for every ref change, --no-verify included, and refuses the change when it exits
# non-zero in the "prepared" state. The guard's copy refuses a move of HEAD or of the branch HEAD is on, outside a scratch
# repository, and writes what it refused to GIT_TRIED beside the hooks, so recording can tell "the agent tried to move
# git history" from "someone committed by hand while it ran" (native_evidence.changes, ``tried``).
REF_HOOK = 'reference-transaction'
GIT_TRIED = 'dervo-git-tried'


# Linux has no /private: its temp folder (tempfile.gettempdir(), added in git_guard) is the scratch root there.
SCRATCH_ROOTS = () if platform_os.LINUX else ('/private/tmp', '/private/var/folders')


def _git_common_dir(project):
    """The real path of the project's shared .git folder (the same for every worktree of it), or None."""
    import os
    import subprocess
    try:
        env = ({**platform_os.provider_environment(), 'HOME': os.path.expanduser('~')} if platform_os.WINDOWS
               else {'PATH': '/usr/bin:/bin', 'HOME': os.path.expanduser('~')})
        result = subprocess.run([platform_os.git_executable(), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                cwd=project, capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    common = result.stdout.strip() if result.returncode == 0 else ''
    return os.path.realpath(common) if common else None


def _hook_path(path):
    """``path`` the way the hook compares it. On Windows that's git's own C:/Users/x form in lower case: Git for
    Windows' sh prints the temp folder as /tmp from ``pwd -P``, so the Mac comparison could never match there, and
    Windows paths don't differ by case."""
    if not platform_os.WINDOWS:
        return path
    return path.replace('\\', '/').lower()


def git_guard(folder, project=None):
    """Git hooks for a full-access run that refuse commit, merge, rebase and push however the command is spelled
    (``git -C . commit``, ``/usr/bin/git push``). Set through GIT_CONFIG_* so the project's own config is untouched;
    Dervo's own commit runs with a clean environment and never sees them. ``--no-verify`` still skips hooks, which is
    why native_evidence also refuses a run that moved HEAD.

    16.2 (Bryce, 26 Sep: "it would be so much nicer if you could just run the commands for me"): with the ``project``
    folder named, commit, merge and rebase work in a scratch repository inside a temp folder (/private/tmp, the user's
    temp folder under /private/var/folders), so test fixtures, evals and feature-checks run inside a run. Never in the
    project, a repository nested inside it, or any worktree sharing its .git. Push stays refused everywhere. Without
    ``project`` every repository is refused, as before."""
    import os
    import shlex
    import tempfile
    hooks = os.path.join(folder, 'dervo-git-hooks')
    os.makedirs(hooks, mode=0o700, exist_ok=True)
    refuse = 'echo "Dervo: commits, merges, rebases and pushes happen in Dervo after the user approves." >&2\nexit 1\n'
    scratch = ''
    if project:
        real = os.path.realpath(project)
        common = _git_common_dir(real)
        common = _hook_path(os.path.normpath(common)) if common else None
        # Dervo's own scratch folder too: a run's folder is there (platform_os.scratch_root), which on a Mac host with no
        # TMPDIR is ~/Library/Caches/Dervo/scratch.
        roots = sorted({*(() if platform_os.WINDOWS else SCRATCH_ROOTS), os.path.realpath(tempfile.gettempdir()), platform_os.scratch_root()})
        roots, real = [_hook_path(root) for root in roots], _hook_path(real)
        # On a Mac, paths are compared after pwd -P, so /tmp and /var/folders links resolve the same way on both sides.
        # On Windows, git's own C:/ paths are compared in lower case (_hook_path).
        resolve = ('top=$(printf %s "$top" | tr A-Z a-z)\ncommon=$(printf %s "$common" | tr A-Z a-z)\n' if platform_os.WINDOWS
                   else 'top=$(cd "$top" && pwd -P) && common=$(cd "$common" && pwd -P) || refuse\n')
        scratch = ('top=$(git rev-parse --show-toplevel 2>/dev/null) || refuse\n'
                   'common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || refuse\n'
                   + resolve
                   + (f'[ "$common" = {shlex.quote(common)} ] && refuse\n' if common else '')
                   + f'case "$top/" in {shlex.quote(real + "/")}*) refuse ;; esac\n'
                   + f'case "$common/" in {shlex.quote(real + "/")}*) refuse ;; esac\n'
                   + 'case "$top/" in ' + '|'.join(shlex.quote(root + '/') + '*' for root in roots) + ') exit 0 ;; esac\n')
    for name in GIT_GUARD_HOOKS:
        path = os.path.join(hooks, name)
        with open(path, 'w') as handle:
            handle.write('#!/bin/sh\nrefuse() {\n' + refuse + '}\n' + ('refuse\n' if name == 'pre-push' else scratch + 'refuse\n'))
        os.chmod(path, 0o700)
    # Only a ref that exists and actually moves counts: ``git stash``'s reset rewrites HEAD with the same commit, and a
    # new worktree's HEAD starts from nothing (all zeros). Other refs (a fetch's remote branches, a new branch, a tag, a
    # stash) pass.
    tried = shlex.quote(_hook_path(os.path.join(folder, GIT_TRIED)) if platform_os.WINDOWS else os.path.join(folder, GIT_TRIED))
    moved = ('head=$(git symbolic-ref -q HEAD 2>/dev/null)\nhit=\'\'\n'
             'while read -r old new ref; do\n'
             '  [ "$old" = "$new" ] && continue\n'
             '  case "$old" in *[!0]*) ;; *) continue ;; esac\n'
             '  if [ "$ref" = HEAD ] || { [ -n "$head" ] && [ "$ref" = "$head" ]; }; then hit="$hit $ref"; fi\n'
             'done\n'
             '[ -z "$hit" ] && exit 0\n'
             f'printf \'%s\\n\' "$hit" >> {tried}\n')
    path = os.path.join(hooks, REF_HOOK)
    with open(path, 'w') as handle:
        handle.write('#!/bin/sh\n[ "$1" = prepared ] || exit 0\nrefuse() {\n' + moved + refuse + '}\n' + scratch + 'refuse\n')
    os.chmod(path, 0o700)
    return {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.hooksPath', 'GIT_CONFIG_VALUE_0': hooks}


def git_tried(folder):
    """The refs the guard in ``folder`` refused to move during a run (reference-transaction), [] when it refused none,
    or None when there's no guard there to ask (no folder, a run that wasn't full access)."""
    import os
    if not folder or not os.path.isdir(os.path.join(folder, 'dervo-git-hooks')):
        return None
    try:
        with open(os.path.join(folder, GIT_TRIED), encoding='utf-8', errors='replace') as handle:
            return sorted({ref for line in handle for ref in line.split()})[:20]
    except FileNotFoundError:
        return []
    except OSError:
        return None


# GitHub CLI variables that carry a sign-in or point gh at one. The launch environment is an allowlist already
# (transport.clean_environment), so none of these gets in; github_guard drops them again in case that ever changes.
GH_SECRETS = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_HOST', 'GH_CONFIG_DIR')


def github_guard(env, folder):
    """Runs don't get your GitHub sign-in handed to them (b16/git survey, section 2, item 7). gh keeps its login in
    hosts.yml under its config folder and the token in the Keychain, keyed by the hosts listed there, and Claude runs get
    your real HOME for the Keychain. So every run gets GH_CONFIG_DIR set to an empty folder of its own, which gh reads
    instead of ~/.config/gh: a plain gh call finds no host and reads as signed out. That stops accidental use. A
    full-access shell runs as you, so a run that unsets the variable, or reads the Keychain another way, can still reach
    the sign-in (security review, 25 Sep, S2); FULL_ACCESS_DETAIL tells you so, and claude_policy.GH_DENIED blocks the
    usual spellings for Claude. Pull requests go through Dervo's own confirms. The folder is inside the run's temporary
    folder and goes with it."""
    import os
    for key in GH_SECRETS:
        env.pop(key, None)
    config = os.path.join(folder, 'dervo-gh')
    os.makedirs(config, mode=0o700, exist_ok=True)
    env['GH_CONFIG_DIR'] = config
    return env


def host_stop_reason(exc):
    """The plain reason when ``exc`` is one of Dervo's own limits (silence, run length, output size), else None."""
    import transport
    text = str(exc)
    if not isinstance(exc, Rejected):
        return None
    if isinstance(exc, HostStop) or text in HOST_STOPS.values() or text in OLD_HOST_STOPS or transport.is_limit_message(text):
        return text
    return None


def host_stop_limit(exc):
    """Which of Dervo's limits ended the run, for the record: idle, hard, stop or output (transport's size limits)."""
    if isinstance(exc, HostStop):
        return exc.limit
    text = str(exc)
    return next((kind for kind, line in HOST_STOPS.items() if line == text), None) or ('output' if host_stop_reason(exc) else None)


class ClaudeCodingTurn:
    """Host-thread pump with the same surface as CodexCodingTurn.

    Order: authenticate through the CLI's own auth status in the owned profile,
    build the restricted command, launch, persist the session ID, complete the
    SDK initialize handshake, then deliver the task context. A stop sends SIGINT
    to the owned process group; the exit status decides interrupted versus
    uncertain. No automatic replay.
    """
    listing_until = None  # a first run waiting for the account's connector list before it launches (beta.15)
    approved_until = 0.0  # codex_coding.recheck_approval: the first tick always checks
    last_seen = None  # wall-clock time of the run's last output once its task was sent (beta.17.1)
    hold = False  # started ahead of time and not yet handed a task (warm_runs, adopt)
    relaunched = False  # beta.18: the one fresh launch after Claude Code started a turn before the task (_relaunch)
    idle_seconds, run_seconds = IDLE_SECONDS, RUN_SECONDS  # the project's limits replace these in __init__ (run_limits)

    def __init__(self, binding, *, task, context, emit, api_key=None, hold=False):
        if not callable(context) or not callable(emit):
            raise Rejected('Trusted task context and event handlers are required')
        if hold and api_key is not None:
            raise Rejected('An API-key account’s Claude Code isn’t started ahead of time')
        self.context, self.emit = context, emit
        # Started ahead of time (warm_runs): launch, handshake and connectors go on, but no task is sent until a real
        # task takes this process (adopt). The pool closes it before this longer deadline runs out.
        self.hold = hold
        self.closed, self.begun, self.finishing, self.error = False, False, False, None
        self.process, self.neutral, self.servers, self.server_config = None, None, None, None
        import warm_runs
        self.deadline = time.monotonic() + (warm_runs.WARM_SECONDS + 60 if hold else 120)
        limits = getattr(task, 'limits', None) or {}
        self.idle_seconds = limits.get('idle_seconds') or IDLE_SECONDS
        self.run_seconds = limits.get('run_seconds', RUN_SECONDS)  # None: this project has no cap on run length
        self.hard_deadline = self.deadline + self.run_seconds if self.run_seconds else None
        self.stopping = False
        self.adapter = ClaudeCodingAdapter(binding, self._write, emit, task=task)
        claude = executable()
        import claude_version
        # A Claude Code too old for Dervo's flags, or for this binding's model, stops here with what to run (A7).
        claude_version.require(claude, model=binding.get('model'))
        # ``api_key`` lives in this process's environment only: never in the binding, the task record or argv.
        # Bounded so --version and auth status together stay under the bridge's 10 seconds for one request.
        import subprocess
        try:
            metadata = auth_status(claude, binding['home'], api_key=api_key, timeout=AUTH_SECONDS)
        except subprocess.TimeoutExpired:
            slow = Rejected('Claude Code took too long to say which account it’s using. Try again in a moment.')
            slow.slow_account = True  # a send waiting on a warm start doesn't launch again and wait twice (take_warm)
            raise slow from None
        self.adapter.authenticate(metadata)
        from provider_plans import plan_of
        if plan_of(metadata):
            emit('plan', {'plan': plan_of(metadata)})  # kept per account, so the picker can warn about Fable's paid credits (A4)
        self.neutral = tempfile.TemporaryDirectory(prefix='dervo-claude-', dir=platform_os.scratch_root())
        self.task, self.binding, self.claude, self.api_key = task, binding, claude, api_key
        if task.full_access:
            self.adapter.shots = self.neutral.name
        if task.full_access or task.connectors_only:
            import mcp_connectors
            if binding.get('method') != 'api_key':  # an API-key account can't use full access at all. Never waits here:
                # the desktop app ends the backend when one request takes over 10 seconds, and listing takes about 9.
                listed = mcp_connectors.names(claude, binding['home'], clean_environment('claude', binding['home'], binding['home']))
                if mcp_connectors.cached(binding['home']) is None and api_key is None:
                    # Beta.15 (Bryce, 24 Sep): the first run after Dervo opens found no list yet, so it launched with no
                    # connector allowed and the first reply said it couldn't reach them. It waits in tick() instead.
                    self.listing_until = time.monotonic() + CONNECTOR_LIST_WAIT_SECONDS
                    emit('connectors', {'state': 'connecting', 'pending': [], 'failed': []})
                    return
                self._pick_connectors(listed)
        self._launch()

    def _pick_connectors(self, listed):
        import mcp_connectors
        allowed = self.task.connectors_allowed
        # Your servers for this project folder, as `claude` here would start them: `claude mcp list` runs in the profile
        # folder, so a local or approved project server only shows up here (mcp_connectors.configured).
        own = mcp_connectors.configured(self.binding['home'], self.binding.get('cwd'),
                                        project_scope=bool(self.task.full_access and getattr(self.task, 'commands', True)))
        listed = list(listed) + [name for name in own if name not in listed]
        # A project that picked its connectors allows exactly those and hides the rest from Claude Code.
        self.adapter.connectors = listed if allowed is None else [name for name in listed if name in allowed]
        self.adapter.hidden_connectors = [] if allowed is None else [name for name in listed if name not in allowed]
        # Only allowed servers are written, so a hidden one is never even started. The file lives in its own private
        # folder, not the screenshot folder the run can read (--add-dir).
        servers = {name: config for name, config in own.items() if name in self.adapter.connectors}
        self.server_config = servers
        if servers and self.servers is None:
            self.servers = tempfile.TemporaryDirectory(prefix='dervo-mcp-', dir=platform_os.scratch_root())
            self.adapter.servers_file = mcp_connectors.write_servers(servers, self.servers.name)

    def _listed(self):
        """While a first run waits for the connector list: True once it's launched (with the list, or without one when
        listing failed or ran out of time), False while the list is still coming. Nothing is sent before launch."""
        import mcp_connectors
        home = self.binding['home']
        listed = mcp_connectors.cached(home)
        if listed is None and mcp_connectors.listing(home) and time.monotonic() < self.listing_until:
            return False
        self.listing_until = None
        self._pick_connectors(listed or [])
        if not self.adapter.connectors:
            # Nothing to connect, so the record doesn't stay on "connecting". With some, the handshake's wait takes over.
            self.emit('connectors', {'state': 'ready' if listed is not None else 'unknown', 'pending': [], 'failed': []})
        self._launch()
        return True

    def _launch(self):
        binding, task, claude, api_key, emit = self.binding, self.task, self.claude, self.api_key, self.emit
        try:
            import session_continuity
            cwd = binding['cwd'] if task.full_access else self.neutral.name
            if self.adapter.fork_from and not session_continuity.place_session(binding['home'], self.adapter.fork_from, cwd):
                # The previous message's session file is gone (or was never written, as when its run failed early). The
                # session it continued from still holds everything before it, so that's tried next (phase 2, item 6).
                parent = binding.get('fork_parent')
                if parent and parent != self.adapter.session_id and session_continuity.place_session(binding['home'], parent, cwd):
                    self.adapter.fork_from = parent
                    emit('continuity', {'state': 'continues', 'session_id': parent, 'parent': True,
                                        'reason': 'The last message’s Claude Code session wasn’t saved, so this one continues from the session before it.'})
                else:
                    self.adapter.fork_from = None
                    emit('continuity', {'state': 'fresh', 'reason': 'The earlier Claude Code session isn’t saved on this Mac any more.'})
            elif self.adapter.resume:
                session_continuity.place_session(binding['home'], self.adapter.session_id, cwd)  # a retry or correction in a new empty folder
            command = self.adapter.command(claude)
            env = clean_environment('claude', binding['home'], binding['home'], api_key=api_key)
            if task.full_access:
                # The project folder is the working directory, and the owner's usual toolchains are on PATH so builds run.
                env['PATH'] = full_access_path(env['PATH'])
                env['DERVO_SCREENSHOTS'] = self.neutral.name
                env.update(git_guard(self.neutral.name, cwd))
                env.update(subagent_environment(binding['model']))  # sub-agents on the run's own model (16.2)
            elif self.adapter.servers_file is not None:
                # A Home job with your servers: no shell, but the servers it starts (npx, uvx, node) need your toolchains.
                env['PATH'] = full_access_path(env['PATH'])
            github_guard(env, self.neutral.name)
            self.process = OwnedProcess(command, cwd=cwd, env=env)
            # ``pid`` is the CLI's process group leader (start_new_session), so a relaunched host could find or end a
            # survivor of a killed host (run-lifecycle map, 1). Saving and using it is the run record's side.
            emit('session', {'session_id': self.adapter.session_id, 'pid': getattr(self.process.process, 'pid', None)})
        except Exception:
            self.close()
            raise

    def _write(self, message):
        if message.get('type') == 'user':
            self.begun = True
        try:
            self.process.send(message)
        except (OSError, TimeoutError) as exc:
            self.error = str(exc)
            self.adapter.state = 'blocked'
            try:
                self.emit('terminal', {'state': 'uncertain' if self.begun else 'failed', 'error': self.error})
            finally:
                self.close()
            raise

    @property
    def pending_tool_requests(self):
        if self.closed:
            return ()
        return self.adapter.pending_tool_requests

    def steer(self, text, images=()):
        if self.closed or self.finishing or self.stopping:
            raise Rejected('This run has finished, so the message waits for the next one.')
        self.adapter.steer(text, images=images)

    def complete_tool_result(self, token, *, success, text):
        if self.closed:
            raise Rejected('Coding process is closed')
        self.adapter.complete_tool_result(token, success=success, text=text)

    def tick(self):
        if self.closed:
            return
        try:
            recheck_approval(self)
            if time.monotonic() >= self.deadline:
                # A Stop the CLI didn't honour is named as that, not as 30 minutes of silence (run-lifecycle map, 7).
                if self.stopping:
                    raise HostStop('stop', HOST_STOPS['stop'])
                if self.begun:
                    raise HostStop('idle', stop_text('idle', self.idle_seconds))
                raise Rejected('Claude Code didn’t start within 2 minutes.')
            if self.begun and self.hard_deadline is not None and time.monotonic() >= self.hard_deadline:
                raise HostStop('hard', stop_text('hard', self.run_seconds))
            if self.listing_until is not None and not self._listed():
                return  # not launched yet: the account's connector list is still coming (beta.15, Home's first message)
            if self.adapter.state == 'launched':
                self.adapter.initialize()
            try:
                batch = self.process.read(.001)
            except TimeoutError:
                batch = []
            if batch is None:
                self.adapter.exited(self.process.process.wait(timeout=5))
                self.close()
                return
            for event in batch:
                self.adapter.receive(event)
            if batch and self.begun and not self.finishing and not self.stopping and not self.adapter.api_error:
                # A long build or test run keeps streaming: the deadline is for silence, not for total length.
                self.deadline = time.monotonic() + self.idle_seconds
                self.last_seen = time.time()
            if self.adapter.result is not None and not self.finishing and not self.adapter.steers_unread:
                # The CLI stays alive for more input. Closing stdin lets it exit; exit status confirms.
                self.finishing = True
                self.process.process.stdin.close()
                self.deadline = time.monotonic() + 30
            elif self.adapter.api_error and not self.finishing:
                # After its own API-error report the CLI normally writes a result and exits. Don't
                # wait the full turn deadline if it hangs instead.
                self.deadline = min(self.deadline, time.monotonic() + 60)
            if self.adapter.state == 'initialized' and not self.begun and self.adapter.poll_connectors():
                pass  # connectors are still connecting: the task goes once they're ready or the wait runs out (dd #8)
            elif self.adapter.state == 'initialized' and not self.begun and not self.hold:
                self.adapter._approved()
                text, client_id = self.context()
                self.adapter.begin(text, client_id)
                self.emit('task_sent', {})  # timing: the moment the task reached Claude Code
                self.deadline = time.monotonic() + self.idle_seconds
                self.hard_deadline = time.monotonic() + self.run_seconds if self.run_seconds else None
                self.last_seen = time.time()
            if self.adapter.state == 'blocked':
                raise Rejected('Coding provider identity, policy or model was rejected')
            if self.adapter.state in TERMINAL:
                self.close()
        except Exception as exc:
            if self.closed:
                return
            if isinstance(exc, StartedEarly):
                early = exc
                try:
                    again = self._relaunch()
                except Exception as failed:
                    again, exc = False, failed  # the second launch itself failed: that's the reason shown
                import sys
                print(f'Claude Code wrote type={early.kind} subtype={early.subtype or "-"} before Dervo sent the task, '
                      + ('so Dervo launched it once more.' if again else 'so the run stopped.'), file=sys.stderr)
                if again:
                    return
            self.error = str(exc)
            # After Claude Code's own API-error report with no tool call outstanding, the
            # turn is over on the provider's word: failed and retryable, not uncertain.
            reported = self.adapter.api_error if not self.adapter._tools else None
            # Dervo stopped it at one of its own limits and no Dervo tool call is in flight: Dervo knows exactly what
            # happened, so the run failed with that reason. It isn't uncertain, and the next message can continue.
            host_stop = self.begun and not self.adapter._tools and host_stop_reason(exc)
            state = 'failed' if reported or not self.begun or host_stop else 'uncertain'
            if not reported and not host_stop:
                import error_reports
                error_reports.capture(exc, source='task', operation='coding_turn', provider='claude')  # Dervo's own failure
            try:
                self.emit('terminal', {'state': state, 'error': reported or host_stop or self.error,
                                       'provider_failure': self.adapter.failure_kind(reported) if reported else None,
                                       'host_stop': state == 'failed' and bool(host_stop),
                                       # Beta.17.1: which limit, when output last came, and a run that never got its task.
                                       'limit': host_stop_limit(exc) if host_stop else None, 'last_seen': self.last_seen,
                                       'not_started': not self.begun})
            finally:
                self.close()

    def adopt(self, binding, task, context, emit):
        """Hand this process, started ahead of time and still holding, to a real task (warm_runs). The caller has
        checked the launch is exactly the one this task would make (warm_runs.signature). The account is checked again
        now, as a cold launch does, and the task's own approval, context and events take over. Nothing was sent before."""
        if self.closed or self.begun or self.stopping or not self.hold:
            raise Rejected('This Claude Code can’t take a task now')
        if not isinstance(task, TrustedTaskBinding) or dict(task.provider_binding) != dict(self.binding) or dict(binding) != dict(self.binding):
            raise Rejected('This Claude Code was started for a different launch')
        if not callable(context) or not callable(emit):
            raise Rejected('Trusted task context and event handlers are required')
        import subprocess
        from connections_ui import signed_in_as
        try:
            metadata = auth_status(self.claude, binding['home'], timeout=AUTH_SECONDS)
        except subprocess.TimeoutExpired:
            slow = Rejected('Claude Code took too long to say which account it’s using. Try again in a moment.')
            slow.slow_account = True  # the caller doesn't launch again in this same request (take_warm)
            raise slow from None
        if not (type(metadata) is dict and signed_in_as(binding, metadata)):
            raise Rejected('The Claude account changed since this Claude Code started')
        self.context, self.emit = context, emit
        self.adapter.emit, self.adapter.task, self.task = emit, task, task
        self.binding, self.adapter.binding = binding, dict(binding)
        limits = getattr(task, 'limits', None) or {}
        self.idle_seconds = limits.get('idle_seconds') or IDLE_SECONDS
        self.run_seconds = limits.get('run_seconds', RUN_SECONDS)
        self.deadline = time.monotonic() + 120
        self.hard_deadline = self.deadline + self.run_seconds if self.run_seconds else None
        self.hold = False
        self.relaunched = False  # a relaunch while it was held doesn't use up the run's own one (code review 8 Oct, 5)
        self.adapter._approved()

    def _relaunch(self):
        """Beta.18: a run refused before its task gets one fresh launch. Nothing was sent, so there's nothing to reconcile.
        Same session ID, account check and command; the record only sees the new process's pid. A second refusal ends
        the run with the named error. Never after the task went, after Stop, or when Claude Code already saved a file for
        this session ID: it refuses --session-id for a session that has one."""
        if self.relaunched or self.begun or self.stopping or not self._session_unsaved():
            return False
        self.relaunched = True
        old = self.adapter
        self.process.close()
        self.process = None
        adapter = ClaudeCodingAdapter(self.binding, self._write, self.emit, task=self.task)
        adapter.session_id, adapter.fork_from = old.session_id, old.fork_from
        adapter.shots, adapter.connectors, adapter.hidden_connectors = old.shots, old.connectors, old.hidden_connectors
        if old.servers_file is not None and getattr(self, 'server_config', None):
            # The first launch's file may already be gone (removed once Claude Code read it), so it's written again.
            import mcp_connectors
            Path(old.servers_file).unlink(missing_ok=True)
            adapter.servers_file = mcp_connectors.write_servers(self.server_config, self.servers.name)
        adapter.state = 'ready'  # authenticate() already checked this run's account a moment ago
        self.adapter = adapter
        self.deadline = time.monotonic() + 120  # its own 2 minutes to start
        self._launch()
        return True

    def git_tried(self):
        """What the git guard refused to move during this full-access run (git_tried), or None without a guard."""
        full = getattr(getattr(getattr(self, 'adapter', None), 'task', None), 'full_access', False)
        return git_tried(self.neutral.name) if full and self.neutral is not None else None

    def _session_unsaved(self):
        import os
        import session_continuity
        if self.adapter.resume:
            return True  # --resume continues the saved session: its file is meant to be there
        cwd = self.binding['cwd'] if self.task.full_access else self.neutral.name
        slug = session_continuity._slug(os.path.realpath(cwd))
        if len(slug) > session_continuity.SLUG_LIMIT:
            return False  # Claude Code shortens the folder name with a hash, so Dervo can't tell
        return not os.path.lexists(os.path.join(self.binding['home'], 'projects', slug, f'{self.adapter.session_id}.jsonl'))

    def stop(self):
        if self.closed or self.stopping:
            return  # a second Stop while the first is still being honoured changes nothing (run-lifecycle map, 3)
        self.stopping = True  # a stopping run gets its short deadline, not another idle window
        if self.adapter.state == 'running':
            try:
                self.adapter.stop()
                self.process.interrupt()
                self.deadline = time.monotonic() + STOP_SECONDS
            except Exception:
                self.close()
                raise
        else:
            try:
                self.emit('terminal', {'state': 'uncertain' if self.begun else 'interrupted',
                                      'error': 'Stopped before provider acknowledgement'})
            finally:
                self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.adapter.cancel_pending_tools()
        finally:
            try:
                if self.process is not None:
                    self.process.close()
            finally:
                try:
                    if self.neutral is not None:
                        self.neutral.cleanup()
                finally:
                    if self.servers is not None:
                        self.servers.cleanup()

    def status(self):
        return {'active': not self.closed, 'phase': self.adapter.state, 'error': self.error,
                'auxiliary_models': list(self.adapter.auxiliary_models)}
