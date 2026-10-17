"""Validated model endpoints and durable explicit routing preferences."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from uuid import UUID

from dervo_connections import Rejected
from release_profiles import require_provider


def model_id(value):
    if (type(value) is not str or len(value) > 200 or not re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+', value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise Rejected('Choose a model from the checked OpenRouter catalog.')
    return value


def provider_id(value):
    if type(value) is not str or len(value) > 150 or not re.fullmatch(r'[a-z0-9][a-z0-9._/-]*', value):
        raise Rejected('Choose a provider from this model’s checked endpoints.')
    return value


def price(value):
    if (type(value) is not str or len(value) > 50
            or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?', value)):
        raise ValueError('Unknown endpoint price')
    try:
        number = Decimal(value)
        if not number.is_finite() or not 0 <= number <= 10000 or number.as_tuple().exponent < -18:
            raise ValueError('Unknown endpoint price')
        return format(number, 'f')
    except InvalidOperation:
        raise ValueError('Unknown endpoint price') from None


def pricing(rates):
    """Keep the highest published rate across context tiers; don't assume discounts.

    A missing request price stays missing. This normalizes the catalog for route
    selection, not a complete before-send cost bound.
    """
    if type(rates) is not dict or not {'prompt', 'completion'} <= rates.keys() or len(rates) > 32:
        raise ValueError('Unknown endpoint price')
    result = {}
    for field, value in rates.items():
        if field == 'overrides':
            continue
        if field == 'discount':
            # Public endpoint metadata uses a numeric discount. Ignoring a valid
            # discount overestimates rates; applying it could under-reserve spend.
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError('Unknown endpoint discount')
            continue
        if type(field) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', field):
            raise ValueError('Unknown endpoint price')
        result[field] = price(value)
    overrides = rates.get('overrides', [])
    if type(overrides) is not list or len(overrides) > 100:
        raise ValueError('Unknown endpoint price tiers')
    for tier in overrides:
        if type(tier) is not dict or not 2 <= len(tier) <= 32:
            raise ValueError('Unknown endpoint price tier')
        threshold = tier.get('min_prompt_tokens')
        if type(threshold) is not int or not 0 <= threshold <= 1000000000:
            raise ValueError('Unknown endpoint price threshold')
        for field, value in tier.items():
            if field == 'min_prompt_tokens':
                continue
            # Unknown tier conditions or new fees need an explicit parser update.
            if field not in result:
                raise ValueError('Unknown endpoint tier field')
            candidate = price(value)
            result[field] = max(result[field], candidate, key=Decimal)
    return result


def endpoints(data, model, key):
    from openrouter_connection import ConnectionError
    try:
        if type(data) is not dict or data.get('id') != model or type(data.get('endpoints')) is not list or len(data['endpoints']) > 1000:
            raise ValueError()
        rows, seen = [], set()
        for row in data['endpoints']:
            tag = provider_id(row['tag'])
            if tag in seen or key in tag or row.get('model_id') != model:
                raise ValueError()
            context = row['context_length']
            maximum = row.get('max_completion_tokens')
            status = row['status']
            parameters = row['supported_parameters']
            if (type(context) is not int or not 1 <= context <= 1000000000
                    or (maximum is not None and (type(maximum) is not int or not 1 <= maximum <= context))
                    or type(status) is not int or type(parameters) is not list or len(parameters) > 100
                    or any(type(p) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', p) for p in parameters)):
                raise ValueError()
            rates = row.get('pricing')
            valid_prices = None
            try:
                valid_prices = pricing(rates)
            except ValueError:
                pass
            eligible = status == 0 and 'max_tokens' in parameters and valid_prices is not None
            reason = '' if eligible else ('Provider unavailable' if status != 0 else
                'Reply limits aren’t supported' if 'max_tokens' not in parameters else 'Pricing needs checking')
            seen.add(tag)
            rows.append({'provider': tag, 'contextTokens': context, 'budgetContextTokens': context, 'maxOutputTokens': maximum,
                         'parameters': sorted(set(parameters)), 'pricing': valid_prices, 'selectable': eligible, 'reason': reason})
        choices = []
        for row in rows:
            # Base slugs include every returned variant in that provider family.
            members = [item for item in rows if item['provider'] == row['provider'] or
                       ('/' not in row['provider'] and item['provider'].startswith(row['provider'] + '/'))]
            choice = dict(row, coveredProviders=[item['provider'] for item in members])
            if len(members) > 1:
                choice['contextTokens'] = min(item['contextTokens'] for item in members)
                choice['budgetContextTokens'] = max(item['budgetContextTokens'] for item in members)
                choice['maxOutputTokens'] = min(item['maxOutputTokens'] or item['contextTokens'] for item in members)
                choice['parameters'] = sorted(set.intersection(*(set(item['parameters']) for item in members)))
                known = all(item['pricing'] is not None for item in members)
                known = known and all(item['pricing'].keys() == row['pricing'].keys() for item in members)
                choice['pricing'] = ({field: max((item['pricing'][field] for item in members), key=Decimal)
                                      for field in row['pricing']} if known else None)
                choice['selectable'] = known and all(item['selectable'] for item in members)
                choice['reason'] = ('' if choice['selectable'] else 'Pricing needs checking across provider variants'
                                    if not known else 'Some provider variants can’t support this route')
            choices.append(choice)
        return {'modelId': model, 'endpoints': choices}
    except Exception:
        raise ConnectionError('response') from None


class Routes:
    def __init__(self, db, resources):
        self.db, self.resources = db, resources
        require_provider('openrouter', resources)
        if db.in_transaction:
            raise Rejected('Finish the current workspace save before opening model settings.')
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS openrouter_routes (owner TEXT PRIMARY KEY, model TEXT NOT NULL, '
                       'provider TEXT NOT NULL, max_output INTEGER NOT NULL, revision INTEGER NOT NULL)')

    def _owner(self, owner):
        require_provider('openrouter', self.resources)
        try:
            if (type(owner) is not tuple or len(owner) != 3 or not re.fullmatch('[a-z0-9]{20}', owner[0])
                    or owner[1] != 'https://' + owner[0] + '.supabase.co/auth/v1' or str(UUID(owner[2])) != owner[2]):
                raise ValueError()
        except Exception:
            raise Rejected('This model selection needs its verified Dervo account.') from None
        return hashlib.sha256(json.dumps(owner, separators=(',', ':')).encode()).hexdigest()

    def status(self, owner):
        key = self._owner(owner)
        row = self.db.execute('SELECT model,provider,max_output,revision FROM openrouter_routes WHERE owner=?', (key,)).fetchone()
        if row is None:
            return {'revision': 0, 'selection': None}
        return {'revision': row[3], 'selection': {'modelId': row[0], 'provider': row[1], 'maxOutputTokens': row[2],
                                               'allowFallbacks': False}}

    def save(self, owner, *, checked, model, provider, max_output, revision):
        key, model, provider = self._owner(owner), model_id(model), provider_id(provider)
        if type(revision) is not int or revision < 0:
            raise Rejected('Reload the current model settings.')
        if not checked or checked['modelId'] != model:
            raise Rejected('Check this model’s providers before saving it.')
        choices = [row for row in checked['endpoints'] if row['provider'] == provider and row['selectable']]
        if len(choices) != 1:
            raise Rejected('That provider isn’t available in the checked model results.')
        endpoint = choices[0]
        maximum = endpoint['maxOutputTokens'] or endpoint['contextTokens']
        if type(max_output) is not int or not 1 <= max_output <= min(maximum, 100000):
            raise Rejected('Choose a reply limit supported by this provider, up to 100,000 tokens.')
        if self.db.in_transaction:
            raise Rejected('Finish the current workspace save before changing models.')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.status(owner)['revision'] != revision:
                raise Rejected('Model settings changed. Reload them before saving.')
            self.db.execute('INSERT INTO openrouter_routes VALUES (?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
                            'model=excluded.model,provider=excluded.provider,max_output=excluded.max_output,revision=excluded.revision',
                            (key, model, provider, max_output, revision + 1))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.status(owner)

    def request_parameters(self, owner):
        selection = self.status(owner)['selection']
        if selection is None:
            raise Rejected('Choose an OpenRouter model and provider before sending.')
        # This builds routing parameters only. Sending still needs current account,
        # key, catalog, price-bound and spending checks at the execution boundary.
        return {'model': selection['modelId'], 'max_tokens': selection['maxOutputTokens'],
                'provider': {'only': [selection['provider']], 'allow_fallbacks': False, 'require_parameters': True}}
