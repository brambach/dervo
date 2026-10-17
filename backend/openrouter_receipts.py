"""Read-only generation receipts. Total provider charge, never a token estimate.

Contract: https://openrouter.ai/docs/api/api-reference/generations/get-generation
Checked 2026-09-15. No raw provider response reaches the renderer.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import re


def generation_id(value):
    from openrouter_connection import ConnectionError
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', value):
        raise ConnectionError('response')
    return value


@dataclass(frozen=True, repr=False)
class Receipt:
    generation: str
    cost_usd: str


def parse(data, expected):
    from openrouter_connection import ConnectionError
    generation_id(expected)
    if type(data) is not dict or data.get('id') != expected:
        raise ConnectionError('response')
    # An external provider key can have costs outside OpenRouter's ledger.
    if data.get('is_byok') is not False:
        raise ConnectionError('usage_external')
    if (type(data.get('cancelled')) is not bool
            or data.get('finish_reason') not in ('stop', 'length', 'tool_calls', 'content_filter', 'error')):
        raise ConnectionError('usage_pending')
    cost = data.get('total_cost')
    if cost is None:
        raise ConnectionError('usage_pending')
    if type(cost) not in (int, Decimal):
        raise ConnectionError('response')
    try:
        cost = Decimal(cost)
        if not cost.is_finite() or not 0 <= cost <= 10000:
            raise ValueError()
        # Ledger nanodollars always round up, never release an unaccounted fraction.
        rounded = cost.quantize(Decimal('0.000000001'), rounding=ROUND_CEILING)
    except Exception:
        raise ConnectionError('response') from None
    return Receipt(expected, format(rounded, 'f'))


def reconcile(client, key, budget, owner, request_id):
    """Only look up the generation durably bound to this owner's sent request.

    A failed or delayed read leaves its reservation intact. There is no resend.
    The future execution worker must verify current account/key ownership before
    calling this helper. It isn't a renderer operation.
    """
    generation = budget.receipt_generation(owner, request_id)
    receipt = client.generation(key, generation)
    if type(receipt) is not Receipt or receipt.generation != generation:
        from openrouter_connection import ConnectionError
        raise ConnectionError('response')
    budget.settle(owner, request_id, cost=receipt.cost_usd, generation=receipt.generation)
    return budget.status(owner)
