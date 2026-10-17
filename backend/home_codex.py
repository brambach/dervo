"""Home's chat on a Codex account (beta.17, section 3). Bryce, 1 Oct 2026: Home's Claude-only limit was the likely reason
testers bounced. Codex ships inside Dervo and Claude Code installs separately, so a new tester often has only Codex.

One call per message, under the rules Home's Claude call keeps (project_orchestrator.command): no tools, no web search, no
project folder, nothing saved, the same snapshot and SCHEMA. It runs the bundled ``codex app-server`` through Dervo's own
CodexAdapter, so it passes the checks every Codex run passes before any project content goes: the binary's version and
the account's own home (initialize), the launch policy (config/read), the account itself (account/read) and the exact
model in its catalog (model/list), then a thread whose model, folder and sandbox are the ones asked for. What differs from
a coding run:

- ``web_search`` is disabled, not cached (POLICY below), and a search that happens anyway ends the call.
- The folder is an empty one in this account's own scratch folder (platform_os.scratch_root), made for this call and
  removed after it, and marked untrusted like every launch's (codex_policy.untrusted_projects), so no AGENTS.md or
  .codex settings can load.
- The thread is ephemeral, Home's instructions go in as developerInstructions (Codex keeps its own base instructions:
  the ChatGPT backend has refused replaced ones), and the turn carries SCHEMA as outputSchema.
- Codex sends the schema to OpenAI's structured outputs in strict mode, where every property has to be required. SCHEMA's
  optional fields go as nullable (strict), and the nulls come off the answer (loose), so it reads like Claude's.

A failure is a sentence for the person. Nothing falls back to another account, model or provider.
"""
import json
import shutil
import tempfile
import time
import uuid

import codex_policy
from adapters import CodexAdapter
from dervo_connections import Rejected

# Bryce's pick for Home's Codex model (2 Oct, from the bundled 0.156.1 catalog). Claude's Home runs on Sonnet, not the
# project's model, for speed and cost; this is the same choice for Codex. The catalog check refuses it when the account
# doesn't offer it, and nothing picks another. gpt-6-luna ("Fast and affordable model for easier tasks") was first: it
# scored about 44/50 on Home's evals against Sonnet's 48/50, so Bryce asked to try gpt-6-sol on the cases it missed.
MODEL = 'gpt-6-sol'  # the catalog: "Workhorse model for coding and everyday work"
MODEL_NAME = 'GPT-6-Sol'  # what Home tells you its replies run on: the catalog's displayName
POLICY = {**codex_policy.POLICY, 'web_search': 'disabled'}
TURN_SECONDS = 150
READ_SECONDS = 1

NO_MODEL = 'Home’s Codex model isn’t set in this build, so nothing was sent.'
SIGNED_OUT = 'This project’s Codex account is signed out. Sign in again in Settings, on Connected accounts, then send it again.'
OTHER_ACCOUNT = ('Codex is signed in to a different account from this project’s, so nothing was sent. Sign in again in '
                 'Settings, on Connected accounts, then send it again.')
PLAN = 'This project’s Codex account is on a ChatGPT plan Dervo doesn’t run on ({}), so nothing was sent.'
API_KEY = 'Codex isn’t using this account’s API key, so nothing was sent. Check the key in Settings, on Connected accounts.'
SETTINGS = 'Codex didn’t take Dervo’s settings for Home on this account, so nothing was sent: {}'
NO_IMAGES = '{} can’t read images on this account, so nothing was sent. Send it without the images.'
NO_ANSWER = 'No answer came back within two minutes. Send it again.'
STOPPED = 'Codex stopped before it answered. Send it again.'
SEARCHED = 'Codex tried to search the web, which Home doesn’t allow, so Dervo stopped it. Send it again.'
UNREADABLE = 'Dervo couldn’t read Codex’s answer. Send it again.'
NOT_STARTED = 'Codex couldn’t be started. Restart Dervo, then send it again.'


class Failed(Exception):
    """Home's Codex call ended without an answer. The message is shown to the person."""


def model_missing(name):
    return (f'{name} isn’t in this Codex account’s model list, so nothing was sent. Home doesn’t switch to another model '
            'on its own.')


NULL_WHEN = ' Null when it doesn’t apply.'
# What a model writes in a field it has to fill but has nothing for. Only an optional field's value is read this way.
PLACEHOLDERS = frozenset(('', 'none', 'null', 'n/a', 'na', '-'))


def strict(schema):
    """SCHEMA as OpenAI's strict structured outputs take it: every object lists all its properties as required, and the
    ones SCHEMA left optional may be null instead, and say so."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if isinstance(schema.get('items'), dict):
        out['items'] = strict(schema['items'])
    properties = schema.get('properties')
    if isinstance(properties, dict):
        needed = set(schema.get('required') or ())
        out['properties'] = {key: strict(value) if key in needed else nullable(strict(value)) for key, value in properties.items()}
        out['required'] = list(properties)
    return out


def nullable(schema):
    out = dict(schema)
    kind = out.get('type')
    if isinstance(kind, str):
        out['type'] = [kind, 'null']
    if isinstance(out.get('enum'), list):
        out['enum'] = [*out['enum'], None]
    # Required everywhere, a field reads as one to fill. On 2 Oct gpt-6-luna filled context_quote, which Claude leaves out
    # unless the message continues an earlier one, and 7 of 50 Home evals held their job for it.
    out['description'] = (out.get('description') or '').rstrip() + NULL_WHEN
    return out


def loose(value, schema=None):
    """The answer as Claude would give it: a field SCHEMA left optional that Codex set to null, or to a placeholder such as
    "" or "none", is left out. ``schema`` is SCHEMA (not strict's copy); without it only nulls go."""
    if isinstance(value, list):
        items = schema.get('items') if isinstance(schema, dict) else None
        return [loose(item, items) for item in value]
    if not isinstance(value, dict):
        return value
    properties = schema.get('properties') if isinstance(schema, dict) else None
    needed = set(schema.get('required') or ()) if isinstance(schema, dict) else set()
    out = {}
    for key, item in value.items():
        if item is None:
            continue
        if (properties is not None and key in properties and key not in needed and isinstance(item, str)
                and item.strip().casefold() in PLACEHOLDERS):
            continue
        out[key] = loose(item, properties.get(key) if properties else None)
    return out


def argv(executable, login_method, cwd):
    """codex_policy.argv with Home's POLICY: the same overrides and app-server launch, web search off."""
    def toml(value):
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
        return json.dumps(value)
    args = [executable]
    for key, value in codex_policy.launched(codex_policy.for_folder(POLICY, cwd), login_method).items():
        args += ['-c', key + '=' + toml(value)]
    return args + ['app-server', '--listen', 'stdio://', '--strict-config']


class Call:
    """What Orchestrator.account gives ask() for a Codex project: the account it checked, and the model and
    instructions this call runs with. No secret: Codex keeps its own sign-in in the account's home."""

    def __init__(self, profile, instructions, schema, model=None, model_name=None, effort=None):
        """``model`` None is Home's default (MODEL); ``effort`` None is the model's own default level in the catalog."""
        self.profile, self.instructions, self.schema = dict(profile), instructions, schema
        self.model = MODEL if model is None else model
        self.model_name = (MODEL_NAME if model is None else model_name) or self.model
        self.effort = effort


class HomeAdapter(CodexAdapter):
    """Dervo's CodexAdapter for one Home call: Home's policy on config/read, an ephemeral thread with Home's instructions,
    and the turn constrained to the schema. Everything it checks before sending is the adapter's own."""

    def __init__(self, binding, write, emit, *, schema, images=False):
        super().__init__(binding, write, emit)
        self.schema, self.images = schema, images
        self.signed_out, self.reported = False, None
        self.answering = None  # the request whose reply is being read, so a refusal names the step that stopped it

    def _verify_config(self, result, login_method):
        codex_policy.verify_config(result, login_method, policy=POLICY, cwd=self.binding['cwd'])

    def _thread_params(self):
        return dict(super()._thread_params(), ephemeral=True, developerInstructions=self.binding['instructions'])

    def request(self, method, params):
        if method == 'thread/start' and self.images:
            # Asked after model/list and before any project content: a model that takes no images says so now.
            entry = next((m for m in self.models if m.get('model') == self.binding['model']), {})
            if 'image' not in (entry.get('inputModalities') or ()):
                raise Failed(NO_IMAGES.format(self.binding['model_name']))
        if method == 'turn/start':
            params = dict(params, outputSchema=self.schema)
        super().request(method, params)

    def receive(self, message):
        self.answering = self.pending.get(message.get('id')) if isinstance(message, dict) and 'method' not in message else None
        if self.answering == 'account/read' and 'result' in message:
            self.reported = (message.get('result') or {}).get('account')
            self.signed_out = not self.reported
        super().receive(message)


def ask(call, env, prompt, *, images=(), timeout=TURN_SECONDS, factory=None, executable=None):
    """Run Home's call on the account in ``call`` and return (the structured answer, the usage Codex reported or None).
    ``images`` are PNG data URLs, as for Claude. Raises Failed with a sentence for you."""
    if not call.model:
        raise Failed(NO_MODEL)
    from transport import OwnedProcess
    import platform_os
    try:
        exe = executable or codex_policy.executable()
    except Rejected as exc:
        raise Failed(str(exc)) from None
    folder = tempfile.mkdtemp(prefix='dervo-home-', dir=platform_os.scratch_root())
    # The adapter checks ``effort`` against the model's catalog entry before the thread starts (effort_problem).
    binding = dict(call.profile, model=call.model, model_name=call.model_name, cwd=folder, session_id=None, effort=call.effort,
                   instructions=call.instructions)
    seen = dict(answer=None, usage=None, terminal=None, searched=False)

    def emit(kind, data):
        if kind == 'snapshot':
            seen['answer'] = data.get('text')
        elif kind == 'usage':
            seen['usage'] = data
        elif kind == 'terminal':
            seen['terminal'] = data
        elif kind == 'native_tool':
            seen['searched'] = True

    process = None
    try:
        try:
            process = (factory or OwnedProcess)(argv(exe, codex_policy.login_method(binding), folder), cwd=folder, env=env)
        except (OSError, Rejected):
            raise Failed(NOT_STARTED) from None
        adapter = HomeAdapter(binding, process.send, emit, schema=strict(call.schema), images=bool(images))
        begun = False
        deadline = time.monotonic() + timeout
        try:
            adapter.connect()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Failed(NO_ANSWER)
                try:
                    batch = process.read(min(remaining, READ_SECONDS))
                except TimeoutError:
                    continue
                if batch is None:
                    raise Failed(STOPPED)
                for event in batch:
                    adapter.receive(event)
                if seen['searched']:
                    raise Failed(SEARCHED)
                if adapter.state == 'blocked':
                    raise Failed(model_missing(call.model_name))
                if adapter.state == 'ready' and not begun:
                    begun = True
                    adapter.begin(prompt, str(uuid.uuid4()), images=tuple(images))
                if adapter.state in ('completed', 'interrupted', 'failed'):
                    break
        except Rejected as exc:
            raise Failed(refusal(adapter, exc)) from None
        except OSError:  # a write to a server that went away: a broken pipe, or input it stopped reading
            raise Failed(STOPPED) from None
    finally:
        if process is not None:
            process.close()
        shutil.rmtree(folder, ignore_errors=True)
    terminal = seen['terminal'] or {}
    if adapter.state != 'completed':
        said = ' '.join(str(terminal.get('error') or '').split())[:200]
        raise Failed(f'Codex answered: {said}' if said else STOPPED)
    try:
        body = json.loads(seen['answer'] or '')
    except (TypeError, ValueError):
        body = None
    if not isinstance(body, dict):
        raise Failed(UNREADABLE)
    import provider_usage
    return loose(body, call.schema), provider_usage.normalise('codex', seen['usage'])


def one_word(value):
    return value if isinstance(value, str) and value.isalnum() and len(value) <= 20 else 'unknown'


def refusal(adapter, exc):
    """A check the adapter refused, as a sentence: which step stopped it decides the words, and nothing was sent."""
    step = adapter.answering
    if step == 'account/read' or (step is None and adapter.state == 'authenticating'):  # or an account/updated mid-check
        if adapter.signed_out:
            return SIGNED_OUT
        account = adapter.reported if isinstance(adapter.reported, dict) else {}
        from connections_ui import same_email
        if (adapter.binding.get('method') != 'api_key' and account.get('type') == 'chatgpt'
                and same_email(account.get('email'), adapter.binding.get('identity'))):
            return PLAN.format(one_word(account.get('planType')))
        return API_KEY if adapter.binding.get('method') == 'api_key' else OTHER_ACCOUNT
    if step in ('initialize', 'config/read', 'thread/start'):
        return SETTINGS.format(exc)
    if adapter.turn_id is None:
        return f'Codex refused the call, so nothing was sent: {exc}'
    return f'Codex stopped: {exc}'
