"""Host-only text/tool request projection and conservative cost reservation.

No key or network access here. Caller must obtain fresh endpoint data, verify
current account/key/route, reserve this bound, then mark sent before any write.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import json
import math

from dervo_connections import Rejected
from openrouter_routes import model_id, price, provider_id


@dataclass(frozen=True, repr=False)
class Prepared:
    body: bytes
    upper_bound_usd: str


def _messages(payload):
    messages = []
    if payload['systemPrompt']:
        messages.append({'role': 'system', 'content': payload['systemPrompt']})
    pending = set()
    used = set()
    for message in payload['messages']:
        role = message['role']
        if role not in ('user', 'assistant', 'tool') or type(message['content']) is not list:
            raise ValueError()
        if pending and role != 'tool':
            raise ValueError()
        texts, calls = [], []
        for part in message['content']:
            if part['type'] == 'text' and role in ('user', 'assistant') and type(part['text']) is str:
                texts.append(part['text'])
            elif part['type'] == 'tool-call' and role == 'assistant':
                call_id, name, args = part['toolCallId'], part['toolName'], part['input']
                if (type(call_id) is not str or not call_id or len(call_id) > 100 or call_id in used
                        or name not in {t['name'] for t in payload['tools']} or type(args) is not dict
                        or part.get('execution') is not None):
                    raise ValueError()
                used.add(call_id); pending.add(call_id)
                calls.append({'id': call_id, 'type': 'function', 'function': {'name': name,
                    'arguments': json.dumps(args, ensure_ascii=False, separators=(',', ':'), allow_nan=False)}})
            elif part['type'] == 'tool-result' and role == 'tool':
                if (part['toolCallId'] not in pending or type(part['output']) is not str
                        or part.get('execution') is not None or type(part.get('isError', False)) is not bool):
                    raise ValueError()
                pending.remove(part['toolCallId'])
                # Chat completions has no tool-result error field. Carry the
                # status explicitly alongside the unchanged host result text.
                content = json.dumps({'success': not part.get('isError', False), 'text': part['output']},
                                     ensure_ascii=False, separators=(',', ':'))
                messages.append({'role': 'tool', 'tool_call_id': part['toolCallId'], 'content': content})
            else:
                # No media, provider-executed tools or private reasoning.
                raise ValueError()
        if role != 'tool':
            row = {'role': role, 'content': ''.join(texts)}
            if calls: row['tool_calls'] = calls
            messages.append(row)
    if pending or not messages:
        raise ValueError()
    return messages


def prepare(payload, selection, checked, *, checked_at, now):
    """Use a fresh host-normalized endpoint, never a renderer-supplied price."""
    try:
        if (type(checked_at) not in (int, float) or type(now) not in (int, float)
                or not math.isfinite(checked_at) or not math.isfinite(now) or not 0 <= now - checked_at <= 60):
            raise ValueError()
        if type(payload) is not dict or set(payload) != {'model', 'systemPrompt', 'messages', 'tools'}:
            raise ValueError()
        model = model_id(payload['model'])
        if model.startswith('openrouter/') or ':online' in model:
            raise ValueError()
        provider = provider_id(selection['provider'])
        if checked['modelId'] != model or type(checked['endpoints']) is not list:
            raise ValueError()
        matches = [row for row in checked['endpoints'] if row['provider'] == provider]
        if len(matches) != 1:
            raise ValueError()
        endpoint = matches[0]
        if (selection['modelId'] != model or selection.get('allowFallbacks') is not False
                or endpoint['provider'] != provider or endpoint['selectable'] is not True
                or type(payload['systemPrompt']) is not str or type(payload['messages']) is not list
                or type(payload['tools']) is not list):
            raise ValueError()
        output, context = selection['maxOutputTokens'], endpoint['budgetContextTokens']
        if (type(output) is not int or type(context) is not int or not 1 <= output <= context <= 1000000000
                or output > (endpoint['maxOutputTokens'] or endpoint['contextTokens'])
                or 'max_tokens' not in endpoint['parameters']
                or (payload['tools'] and 'tools' not in endpoint['parameters'])):
            raise ValueError()
        rates = {key: Decimal(price(value)) for key, value in endpoint['pricing'].items()}
        # Automatic caching can add charges even without cache_control. Missing
        # cache prices aren't zero, and max_price has no cache-rate ceiling.
        if not {'prompt', 'completion', 'input_cache_read', 'input_cache_write'} <= rates.keys():
            raise ValueError()
        # Extra metered capabilities aren't requested. Refuse unfamiliar nonzero
        # charges instead of silently excluding them from the reservation.
        covered = {'prompt', 'completion', 'request', 'input_cache_read', 'input_cache_write'}
        if any(value != 0 for key, value in rates.items() if key not in covered): raise ValueError()
        request_rate = rates.get('request', Decimal(0))
        prompt_rate = rates['prompt'] + rates['input_cache_read'] + rates['input_cache_write']
        # Reserve the full accepted context capacity, not a characters-to-tokens
        # estimate. Output and any documented cache fees are counted separately.
        cost = (context * prompt_rate + output * rates['completion'] + request_rate).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING)
        if not 0 <= cost <= 10000: raise ValueError()
        body = {'model': model, 'messages': _messages(payload), 'max_tokens': output, 'stream': True,
                'provider': {'only': [provider], 'order': [provider], 'allow_fallbacks': False,
                    'require_parameters': True,
                    'max_price': {'prompt': format(rates['prompt'] * 1000000, 'f'),
                                  'completion': format(rates['completion'] * 1000000, 'f'),
                                  'request': format(request_rate, 'f')}}}
        if payload['tools']:
            body['tools'] = [{'type': 'function', 'function': {'name': row['name'], 'description': row['description'],
                             'parameters': row['inputSchema']}} for row in payload['tools']]
        encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 4 * 1024 * 1024: raise ValueError()
        # When the catalog omits a request fee, max_price.request=0 is an explicit
        # routing restriction. It doesn't claim that the missing fee was zero.
        return Prepared(encoded, format(cost, 'f'))
    except Exception:
        raise Rejected('Recheck the OpenRouter model, provider and pricing before sending this task.') from None
