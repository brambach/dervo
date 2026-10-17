"""Codex 0.156.1 coding protocol. Only trusted host code may construct this.

The ordinary chat adapter and all built-in tool restrictions stay unchanged.
Dynamic tool execution belongs to the host broker, never to this module.
"""
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from adapters import CodexAdapter
from dervo_connections import Rejected
from codex_policy import check_profile, login_method
from codex_coding_policy import argv
from transport import OwnedProcess, clean_environment
import re
import tempfile
import platform_os
import time


DEFAULT_TOOLS = ('dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_run_tests')
EDIT_TOOL = 'dervo_edit_file'  # one exact, unique text swap checked against the file's hash (big files in scoped mode, beta.15)
READ_RANGE = ('start_line', 'end_line')  # optional 1-based, inclusive line range of dervo_read_file
LINE_LIMIT = 10000000
HELP_TOOL = 'dervo_request_help'
VERDICT_TOOL = 'dervo_review_verdict'  # an independent reviewer's structured verdict; the only way a review "passes"
# Offered only to a writer whose task carries explicit user-granted folders: bounded listing, literal search and
# absent-before creation inside those folders. Never part of the default file-only contract, never a reviewer's.
FOLDER_TOOLS = ('dervo_list_files', 'dervo_search_files', 'dervo_create_file')
VERDICTS = ('pass', 'findings')
LESSON_TOOL = 'dervo_suggest_lesson'
from task_guide import TOOL as GUIDE_TOOL, LIMITS as GUIDE_LIMITS, OPTIONAL as GUIDE_OPTIONAL
REMEMBER_TOOL, HISTORY_TOOL = 'dervo_remember', 'dervo_search_history'  # project_memory
THREAD_TOOL = 'dervo_propose_thread'
QUESTION_TOOL = 'dervo_ask_user'
REMINDER_TOOL = 'dervo_remind_user'  # task_reminders (beta.16.1)
CHAT_TOOL = 'dervo_check_chat'  # chat_links
NOTE_TOOL = 'dervo_leave_note'  # thread_notes (beta.17)
SKILL_TOOL = 'dervo_propose_skill'  # project_skills
ALL_TOOLS = DEFAULT_TOOLS + (HELP_TOOL, VERDICT_TOOL, LESSON_TOOL, GUIDE_TOOL) + FOLDER_TOOLS + (REMEMBER_TOOL, HISTORY_TOOL, CHAT_TOOL, NOTE_TOOL, THREAD_TOOL, SKILL_TOOL, QUESTION_TOOL, REMINDER_TOOL)
TOOL_KEYS = {'dervo_read_file': {'path'} | set(READ_RANGE), 'dervo_write_file': {'path', 'expected_hash', 'content'}, 'dervo_run_tests': set(),
             EDIT_TOOL: {'path', 'expected_hash', 'old_string', 'new_string'},
             HELP_TOOL: {'agent', 'purpose', 'instructions', 'files'}, VERDICT_TOOL: {'verdict', 'findings'},
             'dervo_list_files': {'directory'}, 'dervo_search_files': {'directory', 'query'},
             'dervo_create_file': {'path', 'content'}, LESSON_TOOL: {'lesson','evidence'}, GUIDE_TOOL: set(GUIDE_LIMITS),
             REMEMBER_TOOL: {'action', 'entry_id', 'text', 'replaces'}, HISTORY_TOOL: {'query'}, CHAT_TOOL: {'name'}, NOTE_TOOL: {'note_key', 'name', 'body', 'waits_for_user'}, THREAD_TOOL: {'proposal_key', 'title', 'purpose', 'request'},
             SKILL_TOOL: {'name', 'description', 'steps', 'note'}, QUESTION_TOOL: {'question_key', 'ask', 'options'},
             REMINDER_TOOL: {'reminder_key', 'on', 'text'}}
# Lesson transport allows a bounded oversized draft so the broker can return a correction
# request without losing the task. Stored lesson/evidence limits remain 4096/800 bytes.
STRING_LIMITS = {'proposal_key':64, 'reminder_key': 64, 'note_key': 64, 'body': 8000, 'on': 16, 'request':16000, 'content': 65536, 'old_string': 65536, 'new_string': 65536, 'instructions': 2000, 'purpose': 500, 'agent': 32, 'verdict': 16, 'findings': 4000, 'query': 256, 'lesson':16384, 'evidence':4096, 'action': 16, 'entry_id': 64, 'replaces': 64, 'text': 1600, 'name': 256, 'description': 960, 'steps': 48000, 'note': 1200}
AGENT_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')


def dynamic_tools(names=None):
    """The host-brokered tool contract. ``names`` selects the tools a task offers (a reviewer gets reads only; an owner
    with allowed agents also gets ``dervo_request_help``); the default is the unchanged three-tool contract."""
    specs = [
        (QUESTION_TOOL, 'Ask the user a requirement or preference question, then continue independent work. '
                       'Returns a saved receipt immediately, not an answer. No permissions, commands or account changes.',
         {'question_key': {'type': 'string', 'maxLength': 64}, 'ask': {'type': 'string', 'maxLength': 200},
          'options': {'type': 'array', 'maxItems': 3, 'items': {'type': 'object', 'additionalProperties': False,
              'properties': {'label': {'type': 'string', 'maxLength': 80}, 'detail': {'type': 'string', 'maxLength': 120}},
              'required': ['label', 'detail']}}}),
        (REMINDER_TOOL, 'Save a reminder for the user on a later day: one short line shown on Home that day, with a '
                        'notification that opens this thread. Returns a saved receipt. It never starts work, sends anything '
                        'or grants access.',
         {'reminder_key': {'type': 'string', 'maxLength': 64, 'description': 'Stable key for this reminder. Reuse it on retries.'},
          'on': {'type': 'string', 'maxLength': 10, 'description': 'The day, as YYYY-MM-DD in the user\'s time zone.'},
          'text': {'type': 'string', 'maxLength': 160, 'description': 'One line of at most 160 characters, in your words.'}}),
        ('dervo_read_file', 'Read an approved task file through the host, or a regular file found inside a granted folder '
                            '(it then joins the task evidence). Returns the whole file\'s sha256 and total_lines. A file over '
                            'about 40 KB comes back in parts: read on with start_line and end_line (1-based, inclusive).',
         {'path': {'type': 'string'},
          'start_line': {'type': 'integer', 'minimum': 1, 'description': 'Optional first line to read, 1-based.'},
          'end_line': {'type': 'integer', 'minimum': 1, 'description': 'Optional last line to read, inclusive.'}}),
        ('dervo_write_file', 'Write an approved task file, or a regular file found inside a granted folder, with an expected SHA-256 hash.',
         {'path': {'type': 'string'}, 'expected_hash': {'type': 'string'}, 'content': {'type': 'string'}}),
        (EDIT_TOOL, 'Change an approved task file, or a regular file found inside a granted folder, by replacing one exact '
                    'piece of its text. old_string must occur in the file exactly once, copied exactly (include surrounding '
                    'lines to make it unique); new_string replaces it and may be empty. expected_hash is the file\'s sha256 '
                    'from dervo_read_file. Works on files up to 16 MiB, so use it for any file too big to write whole.',
         {'path': {'type': 'string'}, 'expected_hash': {'type': 'string'},
          'old_string': {'type': 'string', 'maxLength': STRING_LIMITS['old_string']},
          'new_string': {'type': 'string', 'maxLength': STRING_LIMITS['new_string']}}),
        ('dervo_run_tests', 'Run the host-approved task test command. No command arguments.', {}),
        (GUIDE_TOOL, 'Update the user-facing Task guide with your plan and project-specific manual test steps. '
                     'Call after understanding the request, when you move from reading to editing, before running a check, and before your final answer. '
                     'This is reported guidance, not execution evidence. It cannot complete a task or launch anything.',
         {key: {'type': 'string', 'maxLength': limit, 'description': f'At most {limit} UTF-8 bytes.' +
                (' Numbered manual test or preview steps with expected outcomes; empty until known.' if key == 'test_steps' else
                 ' Two to four words naming this thread\'s work, in sentence case; Dervo uses it once as the thread\'s name.' if key == 'title' else '')}
          for key, limit in GUIDE_LIMITS.items()}),
        (LESSON_TOOL, 'Save one reusable project lesson from this task. It saves at once as a project note that later chats '
                      'get as a background fact, and the person can edit or delete it in Knowledge. It is not a rule. '
                      'Include concrete evidence and any uncertainty. This does not approve a result.',
         {'lesson': {'type':'string','maxLength':4096,'description':'At most 4096 UTF-8 bytes.'},
          'evidence': {'type':'string','maxLength':800,'description':'At most 800 UTF-8 bytes. Use a few concise sentences with source locations and limits.'}}),
        (REMEMBER_TOOL, 'Keep project memory current for later chats in this project: add one short durable fact (like the '
                        'package manager, the test command, a port or a required version, and the file that shows it), or '
                        'replace or remove a saved note by its entry_id. Not task progress. It is saved at once and visible '
                        'to the user, who can remove it.',
         {'action': {'type': 'string', 'enum': ['add', 'replace', 'remove']},
          'entry_id': {'type': 'string', 'maxLength': 64, 'description': 'The [id] of the note to replace or remove. Empty for add.'},
          'text': {'type': 'string', 'maxLength': 400, 'description': 'One sentence of at most 400 characters. Empty for remove.'},
          'replaces': {'type': 'string', 'maxLength': 64, 'description': 'With add only, when this note corrects a saved one: that '
                       'note\'s [id]. The old note is kept aside where the person can undo it. Omit or leave empty otherwise.'}}),
        (HISTORY_TOOL, 'Search earlier chats in this project (requests and final answers) for a few specific words. Returns the '
                       'best matches with when they happened and how they ended, and project notes that match: ones kept but '
                       'not sent with this message, and ones Dervo retired.',
         {'query': {'type': 'string', 'maxLength': 256}}),
        (SKILL_TOOL, 'Propose a reusable skill for this project: a procedure you just worked out that a later chat would follow '
                     'the same way. It applies to later chats about that job straight away, and the user can edit or delete '
                     'it in Knowledge.',
         {'name': {'type': 'string', 'maxLength': 64, 'description': 'Lowercase words joined by hyphens, like add-a-bridge-operation.'},
          'description': {'type': 'string', 'maxLength': 240, 'description': 'One line saying when this skill applies.'},
          'steps': {'type': 'string', 'maxLength': 12000, 'description': 'The steps you actually followed, in order, with file names.'},
          'note': {'type': 'string', 'maxLength': 300, 'description': 'Why it is worth keeping, or empty.'}}),
        (THREAD_TOOL, 'Propose one focused child thread when the user asks to split work. The user reviews and starts it. '
                      'No provider runs from this tool. Up to three proposals per request; children cannot propose more.',
         {'proposal_key': {'type':'string','maxLength':64,'description':'Stable key for this proposal. Reuse it on retries.'},
          'title': {'type':'string','maxLength':100}, 'purpose': {'type':'string','maxLength':500},
          'request': {'type':'string','maxLength':16000,'description':'Complete focused instructions and source references for this child.'}}),
        (CHAT_TOOL, 'Check another chat in this project: what it was asked, whether it is working now, its goal and next step, '
                    'and which files it edited. Use it when the user mentions a chat with # or your work may overlap. '
                    'An empty name lists the project\'s chats.',
         {'name': {'type': 'string', 'maxLength': 256, 'description': 'The chat name, with or without #. Empty to list chats.'}}),
        (NOTE_TOOL, 'Leave a note in another chat of this project when its work needs something from yours. Unless the '
                    'project turned it off, Dervo passes it on by itself and that chat starts a run with the note quoted, '
                    'as from you; otherwise it waits for the user to pass it on. It is never sent as the user\'s words.',
         {'note_key': {'type': 'string', 'maxLength': 64, 'description': 'Stable key for this note. Reuse it on retries.'},
          'name': {'type': 'string', 'maxLength': 256, 'description': 'The chat to leave it in, with or without #.'},
          'body': {'type': 'string', 'maxLength': 2000, 'description': 'The note, in your words, at most 2000 characters.'},
          'waits_for_user': {'type': 'boolean', 'description': 'Optional. True when the user has to decide something before '
                             'that chat acts on it: the note then waits for them instead of passing on by itself.'}}),
        (HELP_TOOL, 'Ask a named helper agent (listed in your context) to review or change the listed approved files. The host '
                    'sends only your purpose, your instructions and a snapshot of those files, runs the helper under its own account, '
                    'and returns its answer and evidence here. Use it only when it genuinely helps; a refusal names the reason.',
         {'agent': {'type': 'string'}, 'purpose': {'type': 'string'}, 'instructions': {'type': 'string'},
          'files': {'type': 'array', 'items': {'type': 'string'}}}),
        (VERDICT_TOOL, 'Record your independent review verdict exactly once: "pass" when the reviewed snapshot has no finding that '
                       'must be corrected, otherwise "findings" with each finding on its own line pointing at exact files and lines. '
                       'The host records it; it changes no file and accepts nothing on its own.',
         {'verdict': {'type': 'string', 'enum': list(VERDICTS)}, 'findings': {'type': 'string', 'maxLength': STRING_LIMITS['findings']}}),
        ('dervo_list_files', 'List the regular files and subdirectories of one granted folder (listed in your context) or a '
                             'directory nested inside it. Relative directory path. Use "." when context explicitly permits project-root access. Bounded; a '
                             'truncated flag says when entries were left out.', {'directory': {'type': 'string'}}),
        ('dervo_search_files', 'Search files nested under one granted folder for a literal substring (not a regex). The host '
                               'caps files, bytes and hits and reports what it scanned.',
         {'directory': {'type': 'string'}, 'query': {'type': 'string', 'maxLength': STRING_LIMITS['query']}}),
        ('dervo_create_file', 'Create one new file inside a granted folder. It must not exist yet and its parent directory '
                              'must already exist; an existing target is a conflict, not an overwrite. The creation is '
                              'recorded as an attempt with an absent-before diff. Existing files are changed only with '
                              'dervo_write_file.', {'path': {'type': 'string'}, 'content': {'type': 'string'}}),
    ]
    for name, _, properties in specs:
        if name == HELP_TOOL:
            for key in ('agent', 'purpose', 'instructions'):
                limit = STRING_LIMITS[key]
                properties[key].update(maxLength=limit, description=f'At most {limit} UTF-8 bytes. Keep this field concise.')
    names = DEFAULT_TOOLS if names is None else tuple(names)
    return [{'type': 'function', 'name': name, 'description': description, 'deferLoading': False,
             'inputSchema': {'type': 'object', 'properties': properties,
                             'required': [key for key in properties if not (name == GUIDE_TOOL and key in GUIDE_OPTIONAL) and not (name == REMEMBER_TOOL and key == 'replaces')
                                          and not (name == NOTE_TOOL and key == 'waits_for_user') and key not in READ_RANGE],
                             'additionalProperties': False}}
            for name, description, properties in specs if name in names]


class ToolPathRejected(Rejected):
    """A well-formed call with an invalid path can be corrected in the same turn."""


def check_path(path):
    if path.startswith('/') or '\\' in path or '\x00' in path or any(x in ('', '.', '..') for x in path.split('/')):
        raise ToolPathRejected('Use a path relative to the project, such as backend/host.py. Absolute paths, empty segments and parent traversal are not allowed. Use directory "." only with the listing or search tool.')


def check_string(value, limit, *, empty=False):
    if type(value) is not str or (not empty and not value) or len(value.encode('utf-8')) > limit:
        raise Rejected('Tool string is missing, invalid or too large')


def validate_arguments(tool, args):
    """Shape and size of one tool call, shared by both provider adapters. Raises Rejected; never coerces."""
    if tool == QUESTION_TOOL:
        from task_questions import validate
        validate(args)
        return
    expected = TOOL_KEYS.get(tool, set())
    if tool == GUIDE_TOOL and type(args) is dict:
        expected = expected - {key for key in GUIDE_OPTIONAL if key not in args}  # a guide update may leave its title out
    if tool == REMEMBER_TOOL and type(args) is dict and 'replaces' not in args:
        expected = expected - {'replaces'}  # a correction names the note it corrects; every other save leaves it out
    if tool == NOTE_TOOL and type(args) is dict and 'waits_for_user' not in args:
        expected = expected - {'waits_for_user'}  # beta.17.1: only a note the user has to decide on says so
    if tool == 'dervo_read_file' and type(args) is dict:
        expected = expected - {key for key in READ_RANGE if key not in args}  # a line range is optional
    if tool not in TOOL_KEYS or type(args) is not dict or set(args) != expected:
        raise Rejected('Unknown tool or unexpected arguments')
    for key, value in args.items():
        if key in READ_RANGE:
            if type(value) is not int or not 1 <= value <= LINE_LIMIT:
                raise Rejected(f'{tool}.{key}: expected a whole line number from 1')
            continue
        if key == 'waits_for_user':
            if type(value) is not bool:
                raise Rejected(f'{tool}.{key}: expected true or false')
            continue
        if key == 'files':
            if type(value) is not list or not 1 <= len(value) <= 32 or any(type(p) is not str for p in value) or len(set(value)) != len(value):
                raise Rejected('Tool files must list 1 to 32 distinct task-relative paths')
            for path in value:
                check_string(path, 4096)
                check_path(path)
            continue
        # A modestly oversized guide reaches the broker for a correctable refusal, like lesson suggestions.
        limit = GUIDE_LIMITS[key] * 4 if tool == GUIDE_TOOL else STRING_LIMITS.get(key, 4096)
        allow_empty = (key in ('content', 'instructions', 'findings', 'new_string') or (tool == GUIDE_TOOL and key in ('test_steps',) + GUIDE_OPTIONAL)
                       or (tool == REMEMBER_TOOL and key in ('entry_id', 'text', 'replaces')) or (tool == CHAT_TOOL and key == 'name')
                       or (tool == SKILL_TOOL and key == 'note'))
        try:
            check_string(value, limit, empty=allow_empty)
        except Rejected as exc:
            # Report trusted field names and limits, never provider-supplied content.
            raise Rejected(f'{tool}.{key}: expected a string within {limit} UTF-8 bytes'
                           + ('' if allow_empty else ' (nonempty)')) from exc
    if 'path' in args:
        check_path(args['path'])
    if 'directory' in args and args['directory'] != '.':
        check_path(args['directory'])  # The host checks whether the recorded task permits project-root access.
    if 'expected_hash' in args and not re.fullmatch('[0-9a-f]{64}', args['expected_hash']):
        raise Rejected('Expected hash must be a lowercase SHA-256 digest')
    if 'agent' in args and not AGENT_NAME.match(args['agent']):
        raise Rejected('Agent names are 1 to 32 lowercase letters, digits or hyphens')
    if 'verdict' in args and args['verdict'] not in VERDICTS:
        raise Rejected('A review verdict is exactly "pass" or "findings"')


# Dervo tool calls per run. Past the limit each call gets an error asking the model to finish; past the ceiling the
# model has ignored that 64 times and the run ends.
TOOL_CALL_LIMIT, TOOL_CALL_CEILING, TOOL_WAITING_LIMIT = 256, 320, 16


def tool_call_problem(tool, args, tools, calls, waiting):
    """What the model got wrong in one tool call, as text it can act on, or None when the call is valid. Both adapters
    send this back as the tool's error result. Protocol problems (replays, IDs, wrong thread or turn) aren't checked here
    and still end the run. Names only trusted tool and field names, never provider-supplied content."""
    if type(tool) is not str or not tool or len(tool.encode('utf-8')) > 256:
        return 'A tool call needs a tool name.'
    if tool not in tools:
        return 'Unknown tool. This task can use: ' + ', '.join(sorted(tools)) + '.'
    if calls >= TOOL_CALL_LIMIT:
        return (f'This run has used all {TOOL_CALL_LIMIT} of its Dervo tool calls. Stop calling tools, finish up and '
                'report what you did and what is left.')
    if waiting >= TOOL_WAITING_LIMIT:
        return f'{TOOL_WAITING_LIMIT} tool calls are already waiting. Wait for their results before calling another.'
    try:
        validate_arguments(tool, args)
    except Rejected as exc:
        text = str(exc)
        if text == 'Unknown tool or unexpected arguments' and tool in TOOL_KEYS:
            keys = ', '.join(sorted(TOOL_KEYS[tool])) or 'no arguments'
            text = f'{tool}: unexpected or missing arguments. It takes {keys}.'
        return text
    return None


@dataclass(frozen=True)
class TrustedTaskBinding:
    """Parent loads this from approved task state, not a renderer payload.

    authorize(task, request_or_none) must return exactly True while approval
    and persisted provider/task binding still hold. It's a fast host-thread
    check, not a file/test executor. None checks preflight/context permission.
    """
    task_id: str
    provider_binding: object
    authorize: object
    tools: tuple = DEFAULT_TOOLS  # the exact tool names this task offers; the host decides per role, never the provider
    images: object = None  # Optional host callback, evaluated after the provider and scope checks.
    full_access: bool = False  # The provider's own tools; the host records changes from git (native_evidence).
    computer_access: bool = False  # Explicit account-bound native file access outside the project.
    connectors_allowed: object = None  # full access: None allows every connector, a list exactly those (coding_connectors_set)
    commands: bool = True  # full access: the provider's own shell. Off (coding_commands_set), Claude Code runs without Bash.
    # Beta.15 (Bryce, 24 Sep): a Home job nobody clicked, where full access is on, gets the allowed connectors but no shell.
    connectors_only: bool = False
    # Beta.17.1: the project's silence and run-length limits as the run started (run_limits.read). None is the defaults.
    limits: object = None

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id or not callable(self.authorize):
            raise Rejected('A trusted task binding and approval checker are required')
        import run_limits
        given = dict(self.limits) if isinstance(self.limits, (dict, MappingProxyType)) else {}
        limits = dict(run_limits.DEFAULTS)
        if given.get('idle_seconds') in run_limits.IDLE_CHOICES:
            limits['idle_seconds'] = given['idle_seconds']
        if 'run_seconds' in given and given['run_seconds'] in run_limits.RUN_CHOICES:
            limits['run_seconds'] = given['run_seconds']
        object.__setattr__(self, 'limits', MappingProxyType({key: limits[key] for key in ('idle_seconds', 'run_seconds')}))
        if type(self.connectors_only) is not bool or (self.connectors_only and (self.full_access or self.provider_binding.get('provider') != 'claude'
                                                                                or self.provider_binding.get('method') == 'api_key')):
            raise Rejected('Connectors without the shell are for a Claude Code subscription task without full access')
        if type(self.computer_access) is not bool or (self.computer_access and (not self.full_access or self.provider_binding.get('provider') != 'claude')):
            raise Rejected('Computer access needs an explicitly approved Claude full-access task')
        if type(self.full_access) is not bool:
            raise Rejected('Full access is an explicit true or false')
        if type(self.commands) is not bool or (not self.commands and self.provider_binding.get('provider') != 'claude'):
            raise Rejected('Full access without commands needs Claude Code. Codex runs at Edit files instead.')
        if self.images is not None and not callable(self.images):
            raise Rejected('Task images must come from the host')
        tools = tuple(self.tools) if isinstance(self.tools, (list, tuple)) else None
        if not tools or any(t not in ALL_TOOLS for t in tools) or len(set(tools)) != len(tools):
            raise Rejected('Task tools must name distinct host tools')
        object.__setattr__(self, 'tools', tools)
        object.__setattr__(self, 'provider_binding', MappingProxyType(deepcopy(dict(self.provider_binding))))


@dataclass(frozen=True)
class ToolRequest:
    token: str
    task_id: str
    request_id: object
    thread_id: str
    turn_id: str
    call_id: str
    tool: str
    arguments: object


class CodexCodingAdapter(CodexAdapter):
    def _verify_config(self, result, login_method):
        from codex_coding_policy import verify_config
        verify_config(result, login_method, full_access=self.task.full_access, cwd=self.binding['cwd'])

    def __init__(self, binding, write, emit, *, task):
        if not isinstance(task, TrustedTaskBinding) or dict(task.provider_binding) != binding:
            raise Rejected('Coding provider binding differs from the approved task')
        if set(binding) & {'executable', 'argv', 'env', 'protocol', 'factory'}:
            raise Rejected('Caller launch overrides are not accepted')
        if binding.get('session_id') is not None:
            self._uuid(binding['session_id'])
        self.task = task
        self.native_items = {}
        if task.full_access:
            if binding.get('method') == 'api_key':
                raise Rejected('Full access isn’t available on an API-key account.')
            self.sandbox_mode = 'danger-full-access'
            self.sandbox_policy = {'type': 'dangerFullAccess'}
            self.approval_policy = 'never'
        self._tools, self._seen_calls, self._seen_requests = {}, set(), set()
        super().__init__(binding, write, emit)
        self._approved()

    @staticmethod
    def _uuid(value):
        import uuid
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Rejected('Provider thread ID must be an exact canonical UUID') from None

    def _approved(self, request=None):
        try:
            if self.task.authorize(self.task, request) is not True:
                raise Rejected('Task scope is no longer approved')
        except Exception:
            self.state = 'blocked'
            self.cancel_pending_tools()
            raise

    def connect(self):
        self._approved()
        if self.state != 'new':
            raise Rejected('Connection already initialized')
        self.state = 'initializing'
        self.request('initialize', {'clientInfo': {'name': 'dervo', 'title': 'Dervo', 'version': '0.1.0'},
                                    'capabilities': {'experimentalApi': True}})

    def _thread_params(self):
        self._approved()
        params = super()._thread_params()
        if not self.session_id:
            params['dynamicTools'] = dynamic_tools(self.task.tools)
        return params

    @property
    def pending_tool_requests(self):
        requests = tuple(self._tools.values())
        for request in requests:
            self._approved(request)
        return requests

    def begin(self, text, client_id):
        self._approved()
        super().begin(text, client_id, images=self.task.images() if self.task.images else ())

    @staticmethod
    def _string(value, limit, *, empty=False):
        if type(value) is not str or (not empty and not value) or len(value.encode('utf-8')) > limit:
            raise Rejected('Tool string is missing, invalid or too large')

    def _tool_request(self, message):
        import re
        import uuid
        if self.state != 'running':
            raise Rejected('Coding tool request has no active turn')
        if set(message) != {'id', 'method', 'params'}:
            raise Rejected('Unexpected tool request fields')
        rid, p = message['id'], message['params']
        if type(rid) is int:
            if not -(2 ** 53) < rid < 2 ** 53:
                raise Rejected('Tool request ID is outside the safe integer range')
        else:
            self._string(rid, 256)
        routing = {'threadId', 'turnId', 'callId'}
        if type(p) is not dict or not routing <= set(p):
            raise Rejected('Malformed tool call fields')
        for key in routing:
            self._string(p[key], 256)
        self._scope(p)
        if p['callId'] in self._seen_calls or rid in self._seen_requests:
            raise Rejected('Replayed tool call')
        if len(self._seen_calls) >= TOOL_CALL_CEILING:
            raise Rejected('The agent kept calling tools after Dervo asked it to finish')
        # Anything the model got wrong gets an error result it can read and correct. Only protocol problems above end the run.
        fields = routing | {'tool', 'arguments'}
        if not fields <= set(p) or set(p) - fields - {'namespace'}:
            problem = 'A tool call needs exactly the fields tool and arguments.'
        elif p.get('namespace') is not None:
            problem = 'Dervo tools have no namespace. Call the tool by its plain name.'
        else:
            problem = tool_call_problem(p['tool'], p['arguments'], self.task.tools, len(self._seen_calls), len(self._tools))
        if problem is not None:
            self._approved()
            self._seen_calls.add(p['callId'])
            self._seen_requests.add(rid)
            self.write({'id': rid, 'result': {'success': False,
                        'contentItems': [{'type': 'inputText', 'text': problem}]}})
            return
        tool, args = p['tool'], p['arguments']
        request = ToolRequest(str(uuid.uuid4()), self.task.task_id, rid, p['threadId'], p['turnId'],
                              p['callId'], tool, MappingProxyType(deepcopy(args)))
        self._approved(request)
        self._seen_calls.add(request.call_id)
        self._seen_requests.add(rid)
        self._tools[request.token] = request

    def _started(self, turn):
        if type(turn) is not dict:
            raise Rejected('Malformed provider turn')
        self._string(turn.get('id'), 256)
        super()._started(turn)

    def receive(self, message):
        if self.state in ('blocked', 'completed', 'interrupted', 'failed'):
            raise Rejected('Coding adapter is no longer active')
        try:
            if self.task.full_access:
                self._approved()
                if isinstance(message, dict) and message.get('method') in ('item/started', 'item/completed'):
                    params = message.get('params') or {}
                    self._scope(params)
                    self._native_item(params.get('item'), 'ended' if message['method'] == 'item/completed' else 'started')
            if isinstance(message, dict) and message.get('method') == 'turn/completed':
                if self.state not in ('running', 'stopping'):
                    raise Rejected('Terminal result has no acknowledged coding turn')
                params = message.get('params')
                if type(params) is not dict or type(params.get('turn')) is not dict:
                    raise Rejected('Malformed terminal coding turn')
                self._uuid(params.get('threadId'))
                self._string(params['turn'].get('id'), 256)
                self._uuid(self.session_id)
                self._string(self.turn_id, 256)
                self._scope({'threadId': params['threadId'], 'turnId': params['turn']['id']})
            if (isinstance(message, dict) and 'method' not in message
                    and type(message.get('id')) in (int, str)
                    and self.pending.get(message['id']) in ('thread/start', 'thread/resume')
                    and 'error' not in message):
                self._uuid(message.get('result', {}).get('thread', {}).get('id'))
            if isinstance(message, dict) and message.get('method') == 'item/tool/call':
                self._tool_request(message)
            else:
                super().receive(message)
                if self.state in ('completed', 'interrupted', 'failed', 'blocked'):
                    self.cancel_pending_tools()
        except Exception:
            self.state = 'blocked'
            self.cancel_pending_tools()
            raise

    def _native_item(self, item, phase):
        """Record command and file activity without command output. Only an explicitly granted task reaches here."""
        if not isinstance(item, dict) or item.get('type') not in ('commandExecution', 'fileChange'):
            return
        item_id = item.get('id')
        if not isinstance(item_id, str) or not item_id or len(item_id) > 128:
            raise Rejected('Native activity needs a bounded item ID')
        if self.state not in ('running', 'stopping'):
            raise Rejected('Native activity has no active turn')
        entries = [(item_id, 'Bash', None, item.get('command'))] if item['type'] == 'commandExecution' else [
            (f'{item_id}:{index}', 'Edit', change.get('path'), change.get('path'))
            for index, change in enumerate((item.get('changes') or [])[:200]) if isinstance(change, dict)]
        for key, name, path, summary in entries:
            if self.native_items.get(key) == 'ended' or (key not in self.native_items and len(self.native_items) >= 500):
                continue
            summary = ' '.join(summary.split())[:300] if isinstance(summary, str) else None
            if key not in self.native_items:
                self.native_items[key] = 'started'
                self.emit('native_tool', dict(id=key, name=name, phase='started', path=path, summary=summary, prose=False))
            if phase == 'ended':
                self.native_items[key] = phase
                self.emit('native_tool', dict(id=key, name=name, phase='ended',
                    is_error=item.get('status') in ('failed', 'declined') or item.get('exitCode') not in (None, 0), summary=summary))

    def complete_tool_result(self, token, *, success, text):
        """Host-thread only. Return actual broker output, never invented success."""
        if type(token) is not str or token not in self._tools or self.state != 'running':
            raise Rejected('Tool completion is unknown, cancelled or stale')
        if type(success) is not bool:
            raise Rejected('Tool success must be a boolean')
        self._string(text, 65536, empty=True)
        request = self._tools[token]
        try:
            self._approved(request)
        except Exception:
            self.state = 'blocked'
            self.cancel_pending_tools()
            raise
        self._scope({'threadId': request.thread_id, 'turnId': request.turn_id})
        result = {'success': success, 'contentItems': [{'type': 'inputText', 'text': text}]}
        import json
        if len(json.dumps({'id': request.request_id, 'result': result}).encode()) > 120000:
            raise Rejected('Encoded tool result exceeds the transport budget')
        del self._tools[token]
        self.write({'id': request.request_id, 'result': result})

    def cancel_pending_tools(self):
        """Invalidates tokens without a false success reply. Parent cancels jobs."""
        tokens = list(self._tools)
        self._tools.clear()
        if tokens:
            self.emit('tools_cancelled', {'tokens': tokens})

    def stop(self):
        self.cancel_pending_tools()
        super().stop()


# The run's end on Codex's side: it went quiet past the deadline, or its process ended without a result.
PROVIDER_ENDS = ('Coding provider deadline exceeded', 'Coding provider disconnected without a terminal result')
# How often the pump re-checks a running turn's approval (the task's scope, full access, the account and model) when
# nothing asks for it. It ran on every 20 ms round for every run, and with five runs that check was over a third of the
# backend's time (beta.16.1, tools/load_thread_scale.py). Starting the task and every tool request still check each time,
# and while a tool call is waiting the pump checks on every round, as before, so a revoked approval ends the turn there.
APPROVAL_RECHECK_SECONDS = 1.0


def recheck_approval(turn):
    """The pump's periodic approval check for ``turn``: every round while a tool call waits, else at most once per
    APPROVAL_RECHECK_SECONDS. Raises as _approved does."""
    now = time.monotonic()
    if turn.adapter._tools or now >= turn.approved_until:
        turn.adapter._approved()
        turn.approved_until = now + APPROVAL_RECHECK_SECONDS


class CodexCodingTurn:
    """Host-thread pump with no database access and no execution overrides.

    context() is a trusted, fast loader returning (text, client_id), called only
    after same-process policy/account/model/thread verification. Parent persists
    session events against this task before supplying context. On resume, parent
    must prove that the exact UUID was originally created for this approved task
    and tool contract. Resume restores persisted tools, it can't redefine them.

    Read pending_tool_requests on the host thread and schedule each token once.
    Broker must recheck approval, paths, hashes and its fixed test allowlist at
    execution time. Workers receive immutable inputs, never SQLite connections.
    Marshal real results back to complete_tool_result on this thread. Handle
    tools_cancelled by terminating sandbox jobs and discarding their results.
    This pump never waits for a broker job and never fabricates a tool result.
    """
    approved_until = 0.0  # recheck_approval: the first tick always checks
    # Beta.17.1: the defaults until __init__ reads the project's limits (run_limits). No cap or output seen yet.
    idle_seconds, run_seconds, hard_deadline, last_seen = 7200, 86400, None, None

    def __init__(self, binding, *, task, context, emit):
        if not callable(context) or not callable(emit):
            raise Rejected('Trusted task context and event handlers are required')
        self.context, self.emit = context, emit
        self.closed, self.begun, self.error = False, False, None
        self.process, self.neutral = None, None
        self.deadline = time.monotonic() + 120
        self.stopping = False
        # Beta.17.1: the project's limits (run_limits). Codex had 30 minutes of silence and no cap on length before.
        limits = getattr(task, 'limits', None) or {}
        import run_limits
        self.idle_seconds = limits.get('idle_seconds') or run_limits.DEFAULTS['idle_seconds']
        self.run_seconds = limits.get('run_seconds', run_limits.DEFAULTS['run_seconds'])
        self.hard_deadline, self.last_seen = None, None
        # Validate the binding before opening a child or touching a profile.
        def provider_event(kind, data):
            if kind == 'session' and task.full_access:
                data = {**data, 'pid': getattr(getattr(self.process, 'process', None), 'pid', None)}
            emit(kind, data)
        self.adapter = CodexCodingAdapter(binding, self._write, provider_event, task=task)
        check_profile(binding['home'])
        try:
            self.neutral = tempfile.TemporaryDirectory(prefix='dervo-coding-', dir=platform_os.scratch_root())
            env = clean_environment('codex', binding['home'], binding['home'])
            from claude_coding import github_guard
            if task.full_access:
                from claude_coding import git_guard, full_access_path
                env['PATH'] = full_access_path(env['PATH'])
                env.update(git_guard(self.neutral.name, binding['cwd']))
            github_guard(env, self.neutral.name)  # commands see it through the policy's include_only (codex_coding_policy)
            self.process = OwnedProcess(argv(login_method=login_method(binding), full_access=task.full_access, cwd=binding['cwd']), cwd=self.neutral.name,
                                        env=env, **({'native_commands': True} if task.full_access else {}))
            self.adapter.connect()
        except Exception:
            self.close()
            raise

    def _write(self, message):
        if message.get('method') == 'turn/start':
            # A failed write may already have delivered part or all of the turn.
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
                from claude_coding import HostStop, stop_text
                if self.begun and not self.stopping:
                    raise HostStop('idle', stop_text('idle', self.idle_seconds, who='Codex'))
                if self.begun and self.stopping:
                    # Beta.18: Codex didn't finish within 10 s of your Stop, so Dervo ended it. Still your stop, as for
                    # Claude Code (run_ending: stopped). Up to 17.1 it went unknown ("Coding provider deadline exceeded").
                    raise HostStop('stop', stop_text('stop', who='Codex'))
                raise Rejected(PROVIDER_ENDS[0])
            if self.begun and not self.stopping and self.hard_deadline is not None and time.monotonic() >= self.hard_deadline:
                from claude_coding import HostStop, stop_text
                raise HostStop('hard', stop_text('hard', self.run_seconds))
            try:
                batch = self.process.read(.001)
            except TimeoutError:
                batch = []
            if batch is None:
                raise Rejected(PROVIDER_ENDS[1])
            for event in batch:
                self.adapter.receive(event)
                if self.adapter.state in ('completed', 'interrupted', 'failed'):
                    self.close()
                    return
            if batch and self.begun and self.adapter.state == 'running' and not self.stopping:
                # Silence ends a run, not length: a long task that keeps working keeps its turn.
                self.deadline = time.monotonic() + self.idle_seconds
                self.last_seen = time.time()
            if self.adapter.state == 'ready' and not self.begun:
                self.adapter._approved()
                text, client_id = self.context()
                self.adapter.begin(text, client_id)
                self.begun = True
                self.deadline = time.monotonic() + self.idle_seconds
                self.hard_deadline = time.monotonic() + self.run_seconds if self.run_seconds else None
                self.last_seen = time.time()
            if self.adapter.state == 'blocked':
                raise Rejected('Coding provider identity, policy or model was rejected')
            if self.adapter.state in ('completed', 'interrupted', 'failed'):
                self.close()
        except Exception as exc:
            if self.closed:
                return
            self.error = str(exc)
            from claude_coding import HostStop
            # Beta.17.1: Dervo ended it at one of its own limits with no Dervo tool call in flight, so Dervo knows what
            # happened: failed at that limit, and the next message continues, as for Claude Code. A Bash command Codex
            # was running ends with its process, which the record says (native steps are marked cancelled).
            # Beta.18: transport's output limit counts too, as for Claude Code (Codex went unknown there up to 17.1).
            from claude_coding import host_stop_limit, host_stop_reason
            host_stop = self.begun and bool(host_stop_reason(exc)) and not self.adapter._tools
            # Codex reports its own errors as a failed turn, not an exception. Apart from its silence or its exit, anything
            # raised here is Dervo's own failure.
            if not (isinstance(exc, Rejected) and str(exc) in PROVIDER_ENDS) and not host_stop_reason(exc):
                import error_reports
                error_reports.capture(exc, source='task', operation='coding_turn', provider='codex')
            try:
                self.emit('terminal', {'state': 'failed' if host_stop or not self.begun else 'uncertain', 'error': self.error,
                                       'host_stop': bool(host_stop), 'limit': host_stop_limit(exc) if host_stop else None,
                                       'last_seen': self.last_seen, 'not_started': not self.begun})
            finally:
                self.close()

    def git_tried(self):
        """Beta.18: what the git guard refused to move during this full-access run (claude_coding.git_tried), or None."""
        from claude_coding import git_tried
        full = getattr(getattr(getattr(self, 'adapter', None), 'task', None), 'full_access', False)
        return git_tried(self.neutral.name) if full and self.neutral is not None else None

    def steer(self, text):
        if self.closed or self.stopping:
            raise Rejected('This run has finished, so the message waits for the next one.')
        self.adapter.steer(text)

    def stop(self):
        if self.closed or self.stopping:
            return  # a second Stop while the first is still being honoured changes nothing (run-lifecycle map, 3)
        self.stopping = True  # a stopping run gets its short deadline, not another idle window
        if self.adapter.state == 'running':
            try:
                self.adapter.stop()
                self.deadline = time.monotonic() + 10
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
                if self.neutral is not None:
                    self.neutral.cleanup()

    def status(self):
        return {'active': not self.closed, 'phase': self.adapter.state, 'error': self.error}
