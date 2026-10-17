"""One host-owned OpenRouter POST. No retries, keys in children or raw errors.

Contract checked against OpenRouter's API reference and streaming docs on
2026-09-15. The caller owns current permissions and durable spending. It must
reserve before starting, mark sent in before_send, and reconcile the returned
generation before releasing finish to Cline. A failure after before_send leaves
the charge uncertain. This module isn't a renderer operation.
"""
from dataclasses import dataclass
import json
import time
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from dervo_connections import Rejected
from openrouter_connection import NoRedirect, valid_key
from openrouter_receipts import generation_id
from openrouter_request import Prepared
from release_profiles import require_provider
from supabase_client import account_https_context

LIMIT = 4 * 1024 * 1024
LINE_LIMIT = 256 * 1024
STOPPED = 'The OpenRouter response stopped. Check its recorded usage before continuing.'


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid(_):
        raise ValueError()
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


@dataclass(frozen=True)
class Finished:
    generation: str
    reason: str


class Decoder:
    """Project public text/tool fragments only. Hold finish until the stream ends."""
    def __init__(self, model, on_generation):
        self.model, self.on_generation = model, on_generation
        self.generation = None
        self.reason = None
        self.usage = False
        self.done = False

    def bind(self, value):
        value = generation_id(value)
        if self.generation is None:
            self.on_generation(value)
            self.generation = value
        elif value != self.generation:
            raise ValueError()

    def consume(self, raw):
        if self.done:
            raise ValueError()
        if raw == '[DONE]':
            if not self.generation or self.reason is None:
                raise ValueError()
            self.done = True
            return []
        row = _json(raw)
        if type(row) is not dict:
            raise ValueError()
        # Bind even a failed generation before refusing the response. Its charge
        # can then be looked up without guessing or resending the original task.
        self.bind(row['id'])
        if (row.get('error') is not None or row.get('model') != self.model
                or row.get('object') != 'chat.completion.chunk'):
            raise ValueError()
        choices = row['choices']
        if type(choices) is not list or len(choices) != 1:
            raise ValueError()
        choice = choices[0]
        if type(choice) is not dict or type(choice.get('index')) is not int or choice['index'] != 0:
            raise ValueError()
        delta = choice['delta']
        if type(delta) is not dict or delta.get('role', 'assistant') != 'assistant':
            raise ValueError()
        if any(value not in (None, '', []) for key, value in delta.items()
               if key not in ('role', 'content', 'tool_calls')):
            # Unsupported media, reasoning and provider-side execution stay out
            # of the loop and UI. Don't silently discard required model state.
            raise ValueError()
        content, calls = delta.get('content'), delta.get('tool_calls')
        reason = choice.get('finish_reason')
        usage = row.get('usage')
        if usage is not None and (type(usage) is not dict or self.usage):
            raise ValueError()
        if self.reason is not None:
            # OpenRouter repeats finish_reason in its final accounting frame.
            if usage is None or reason != self.reason or content not in (None, '') or calls not in (None, []):
                raise ValueError()
            self.usage = True
            return []
        events = []
        if content is not None:
            if type(content) is not str:
                raise ValueError()
            if content:
                events.append({'type': 'text-delta', 'text': content})
        if calls is not None:
            if type(calls) is not list or len(calls) > 64:
                raise ValueError()
            for call in calls:
                if (type(call) is not dict or set(call) - {'index', 'id', 'type', 'function'}
                        or type(call.get('index')) is not int or not 0 <= call['index'] < 64
                        or call.get('type', 'function') != 'function'):
                    raise ValueError()
                function = call.get('function', {})
                if type(function) is not dict or set(function) - {'name', 'arguments'}:
                    raise ValueError()
                event = {'type': 'tool-call-delta', 'index': call['index']}
                for source, target, container in (('id', 'toolCallId', call), ('name', 'toolName', function),
                                                   ('arguments', 'inputText', function)):
                    if source in container:
                        if type(container[source]) is not str:
                            raise ValueError()
                        event[target] = container[source]
                events.append(event)
        if reason is not None:
            if reason not in ('stop', 'tool_calls'):
                raise ValueError()
            self.reason = reason
        if usage is not None:
            if self.reason is None:
                raise ValueError()
            self.usage = True
        return events


class StreamClient:
    def __init__(self, resources, *, opener=None, clock=time.monotonic):
        require_provider('openrouter', resources)
        self.resources, self.clock = resources, clock
        self.opener = opener if opener is not None else build_opener(
            ProxyHandler({}), NoRedirect(), HTTPSHandler(context=account_https_context()))

    def run(self, key, prepared, *, allowed, before_send, on_generation, emit):
        """Callbacks stay in the caller's thread. Never pass SQLite to a worker.

        allowed must check cancellation and the captured account/key/route grant.
        The transport calls it before the POST and between every response line.
        before_send must commit the reservation's sent state before returning.
        The caller must check authorization again before accepting queued events.
        """
        require_provider('openrouter', self.resources)
        key = valid_key(key)
        deadline = self.clock() + 120

        def check():
            require_provider('openrouter', self.resources)
            if allowed() is not True or self.clock() >= deadline:
                raise ValueError()

        try:
            if type(prepared) is not Prepared or type(prepared.body) is not bytes or len(prepared.body) > LIMIT:
                raise ValueError()
            body = _json(prepared.body)
            if body.get('stream') is not True or type(body.get('model')) is not str:
                raise ValueError()
            check()
            request = Request('https://openrouter.ai/api/v1/chat/completions', data=prepared.body,
                              headers={'Authorization': 'Bearer ' + key, 'Accept': 'text/event-stream',
                                       'Content-Type': 'application/json'}, method='POST')
            def bind(value):
                if type(value) is not str or key in value:
                    raise ValueError()
                on_generation(generation_id(value))
            decoder = Decoder(body['model'], bind)
            text_tail, tool_events = '', []

            def public(value):
                if type(value) is str:
                    if key in value:
                        raise ValueError()
                elif type(value) is dict:
                    for name, child in value.items():
                        public(name); public(child)
                elif type(value) is list:
                    for child in value: public(child)

            def deliver(event):
                nonlocal text_tail
                public(event)
                if event['type'] == 'tool-call-delta':
                    tool_events.append(event)
                    return
                text_tail += event['text']
                public(text_tail)
                # Hold a possible secret prefix across text frames. Checking
                # escaped JSON or each delta alone misses split credentials.
                keep = next((size for size in range(min(len(text_tail), len(key) - 1), 0, -1)
                             if text_tail.endswith(key[:size])), 0)
                count = len(text_tail) - keep
                if count:
                    emit({'type': 'text-delta', 'text': text_tail[:count]})
                    text_tail = text_tail[count:]

            before_send()
            check()
            # A single fixed destination, with redirects and environment proxies
            # disabled. A failed POST is never retried here or by a provider SDK.
            with self.opener.open(request, timeout=15) as response:
                check()
                header = response.headers.get('X-Generation-Id')
                if header is not None:
                    decoder.bind(header)
                if response.status != 200 or response.headers.get_content_type() != 'text/event-stream':
                    raise ValueError()
                total, frame_bytes, data = 0, 0, []
                while True:
                    check()
                    line = response.readline(LINE_LIMIT + 1)
                    check()
                    if not line:
                        if data or not decoder.done:
                            raise ValueError()
                        break
                    total += len(line)
                    if len(line) > LINE_LIMIT or total > 8 * LIMIT:
                        raise ValueError()
                    text = line.decode('utf-8').rstrip('\r\n')
                    if text == '':
                        if data:
                            raw = '\n'.join(data)
                            for event in decoder.consume(raw):
                                check()
                                deliver(event)
                            data, frame_bytes = [], 0
                    elif text.startswith(':'):
                        continue
                    elif text.startswith('data:'):
                        value = text[5:]
                        if value.startswith(' '): value = value[1:]
                        frame_bytes += len(line)
                        if frame_bytes > LIMIT:
                            raise ValueError()
                        data.append(value)
                    else:
                        # No custom SSE event IDs or reconnect/retry policy.
                        raise ValueError()
                check()
                # Tool arguments are JSON strings and can encode a credential
                # with Unicode escapes split across several provider frames.
                arguments = {}
                for event in tool_events:
                    index = event['index']
                    arguments[index] = arguments.get(index, '') + event.get('inputText', '')
                for raw in arguments.values():
                    public(raw)
                    public(_json(raw))
                for event in tool_events:
                    check()
                    emit(event)
                if text_tail:
                    check()
                    emit({'type': 'text-delta', 'text': text_tail})
                return Finished(decoder.generation, 'tool-calls' if decoder.reason == 'tool_calls' else 'stop')
        except HTTPError as error:
            try:
                header = error.headers.get('X-Generation-Id') if error.headers else None
                if header is not None:
                    decoder.bind(header)
            except Exception:
                pass  # Keep the charge uncertain if its ID can't be retained.
            error.close()
            raise Rejected(STOPPED) from None
        except Exception:
            raise Rejected(STOPPED) from None
