"""Local owner-scoped spending reservations. Only the host records provider receipts."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from dervo_connections import Rejected
from release_profiles import require_provider

SCALE = 1_000_000_000
MAX_USD = 10_000


def amount(value, *, cap=False):
    pattern = r'(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,2})?' if cap else r'(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,9})?'
    if type(value) is not str or not re.fullmatch(pattern, value):
        raise Rejected('Enter a valid dollar amount.')
    number = Decimal(value)
    if number > MAX_USD or (cap and number < Decimal('0.01')):
        raise Rejected('Choose a limit between $0.01 and $10,000.')
    return int(number * SCALE)


def dollars(value):
    return format(Decimal(value) / SCALE, 'f')


def identifier(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except Exception:
        raise Rejected('This spending record needs a valid request ID.') from None
    return value


class Budget:
    def __init__(self, db, *, resources):
        self.db, self.resources = db, Path(resources)
        self._allowed()
        if self.db.in_transaction:
            raise Rejected('Finish the current workspace save before opening spending settings.')
        # CREATE statements don't erase previous reservations or receipts.
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS openrouter_budgets ('
                            'owner TEXT PRIMARY KEY, total INTEGER NOT NULL, per_request INTEGER NOT NULL, revision INTEGER NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS openrouter_spend ('
                            'id TEXT PRIMARY KEY, owner TEXT NOT NULL, reserved INTEGER NOT NULL, '
                            'state TEXT NOT NULL, actual INTEGER, generation TEXT, '
                            'UNIQUE(owner,generation))')

    def _allowed(self):
        require_provider('openrouter', self.resources)

    def _owner(self, owner):
        self._allowed()
        if (type(owner) is not tuple or len(owner) != 3 or type(owner[0]) is not str
                or not re.fullmatch('[a-z0-9]{20}', owner[0])
                or owner[1] != 'https://' + owner[0] + '.supabase.co/auth/v1'):
            raise Rejected('This budget needs its verified Dervo account.')
        identifier(owner[2])
        return hashlib.sha256(json.dumps(owner, separators=(',', ':')).encode()).hexdigest()

    @contextmanager
    def _transaction(self):
        # Obtain the write lock before reading totals so two reservations can't
        # both spend the same remaining allowance through separate connections.
        if self.db.in_transaction:
            raise Rejected('Finish the current workspace save before changing spending.')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _totals(self, owner):
        rows = self.db.execute('SELECT state,reserved,actual FROM openrouter_spend WHERE owner=?', (owner,)).fetchall()
        used = sum(row[2] for row in rows if row[0] == 'settled')
        held = sum(row[1] for row in rows if row[0] in ('reserved', 'sent', 'unknown'))
        uncertain = sum(row[0] == 'unknown' for row in rows)
        overrun = any(row[0] == 'settled' and row[2] > row[1] for row in rows)
        return used, held, uncertain, overrun

    def status(self, owner):
        owner = self._owner(owner)
        config = self.db.execute('SELECT total,per_request,revision FROM openrouter_budgets WHERE owner=?', (owner,)).fetchone()
        used, held, uncertain, overrun = self._totals(owner)
        return {'configured': config is not None, 'revision': config[2] if config else 0,
                'totalUsd': dollars(config[0]) if config else None,
                'perRequestUsd': dollars(config[1]) if config else None,
                'usedUsd': dollars(used), 'reservedUsd': dollars(held),
                'remainingUsd': dollars(max(0, config[0] - used - held)) if config else None,
                'uncertainRequests': uncertain, 'costExceededReservation': overrun,
                'scope': 'This Dervo account in this local workspace. Other apps and Macs have separate spending.'}

    def configure(self, owner, *, total, per_request, revision):
        key = self._owner(owner)
        total, per_request = amount(total, cap=True), amount(per_request, cap=True)
        if per_request > total:
            raise Rejected('The request limit can’t exceed the workspace budget.')
        if type(revision) is not int or revision < 0:
            raise Rejected('Reload the current spending settings.')
        with self._transaction():
            row = self.db.execute('SELECT revision FROM openrouter_budgets WHERE owner=?', (key,)).fetchone()
            if revision != (row[0] if row else 0):
                raise Rejected('Spending settings changed. Reload them before saving.')
            used, held, _, _ = self._totals(key)
            if total < used + held:
                raise Rejected('This budget is below spending already used or reserved.')
            self.db.execute('INSERT INTO openrouter_budgets VALUES (?,?,?,?) ON CONFLICT(owner) '
                            'DO UPDATE SET total=excluded.total,per_request=excluded.per_request,revision=excluded.revision',
                            (key, total, per_request, revision + 1))
        return self.status(owner)

    def reserve(self, owner, request_id, upper_bound):
        """Call with a host-verified price bound, never a renderer cost estimate."""
        key, request_id, bound = self._owner(owner), identifier(request_id), amount(upper_bound)
        with self._transaction():
            if self.db.execute('SELECT 1 FROM openrouter_spend WHERE id=?', (request_id,)).fetchone():
                raise Rejected('This request already has a spending record. Don’t resend it.')
            limits = self.db.execute('SELECT total,per_request FROM openrouter_budgets WHERE owner=?', (key,)).fetchone()
            if limits is None:
                raise Rejected('Set your OpenRouter spending limits before sending a request.')
            used, held, uncertain, overrun = self._totals(key)
            if uncertain or overrun:
                raise Rejected('Check unresolved OpenRouter charges before starting another request.')
            if bound > limits[1] or used + held + bound > limits[0]:
                raise Rejected('This request exceeds your remaining OpenRouter budget or request limit.')
            self.db.execute('INSERT INTO openrouter_spend VALUES (?,?,?,?,NULL,NULL)', (request_id, key, bound, 'reserved'))

    def _record(self, owner, request_id):
        row = self.db.execute('SELECT reserved,state,actual,generation FROM openrouter_spend WHERE id=? AND owner=?',
                              (identifier(request_id), owner)).fetchone()
        if row is None:
            raise Rejected('This spending record doesn’t belong to the current account.')
        return row

    def mark_sent(self, owner, request_id):
        key = self._owner(owner)
        with self._transaction():
            record = self._record(key, request_id)
            if record[1] != 'reserved':
                raise Rejected('This request can’t be sent again.')
            limits = self.db.execute('SELECT total,per_request FROM openrouter_budgets WHERE owner=?', (key,)).fetchone()
            used, held, uncertain, overrun = self._totals(key)
            if limits is None or record[0] > limits[1] or used + held > limits[0] or uncertain or overrun:
                raise Rejected('Spending settings or charges changed. Check them before sending this request.')
            # Durable before the network write. A crash after this point is uncertain.
            self.db.execute('UPDATE openrouter_spend SET state=? WHERE id=?', ('sent', request_id))

    def cancel_unsent(self, owner, request_id):
        key = self._owner(owner)
        with self._transaction():
            if self._record(key, request_id)[1] != 'reserved':
                raise Rejected('This request may already have a charge. Check its usage first.')
            self.db.execute('UPDATE openrouter_spend SET state=? WHERE id=?', ('cancelled', request_id))

    def uncertain(self, owner, request_id):
        key = self._owner(owner)
        with self._transaction():
            if self._record(key, request_id)[1] not in ('sent', 'unknown'):
                raise Rejected('This spending record is already finished.')
            self.db.execute('UPDATE openrouter_spend SET state=? WHERE id=?', ('unknown', request_id))

    def recover(self, owner):
        """Only after the host owns the workspace and prior workers are gone."""
        key = self._owner(owner)
        with self._transaction():
            self.db.execute("UPDATE openrouter_spend SET state='cancelled' WHERE owner=? AND state='reserved'", (key,))
            self.db.execute("UPDATE openrouter_spend SET state='unknown' WHERE owner=? AND state='sent'", (key,))

    def bind_generation(self, owner, request_id, generation):
        """Persist the response ID before fetching its charge. It can't be reassigned."""
        from openrouter_receipts import generation_id
        key, generation = self._owner(owner), generation_id(generation)
        with self._transaction():
            row = self._record(key, request_id)
            if row[1] not in ('sent', 'unknown', 'settled') or (row[3] is not None and row[3] != generation):
                raise Rejected('This request can’t be assigned that usage result.')
            existing = self.db.execute('SELECT id FROM openrouter_spend WHERE owner=? AND generation=?', (key, generation)).fetchone()
            if existing and existing[0] != request_id:
                raise Rejected('That usage result is already assigned to another request.')
            self.db.execute('UPDATE openrouter_spend SET generation=? WHERE id=?', (generation, request_id))

    def receipt_generation(self, owner, request_id):
        key = self._owner(owner)
        row = self._record(key, request_id)
        if row[1] not in ('sent', 'unknown', 'settled') or row[3] is None:
            raise Rejected('The provider response ID is unavailable. Its budget stays reserved.')
        return row[3]

    def settle(self, owner, request_id, *, cost, generation):
        """Only verified provider usage can release a held reservation."""
        key, cost = self._owner(owner), amount(cost)
        if type(generation) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', generation):
            raise Rejected('OpenRouter usage needs a verified generation ID.')
        with self._transaction():
            row = self._record(key, request_id)
            if row[3] is not None and row[3] != generation:
                raise Rejected('That usage result doesn’t match the saved provider response.')
            if row[1] == 'settled' and (row[2], row[3]) == (cost, generation):
                return
            if row[1] not in ('sent', 'unknown'):
                raise Rejected('This spending record can’t accept that usage result.')
            existing = self.db.execute('SELECT id FROM openrouter_spend WHERE owner=? AND generation=?', (key, generation)).fetchone()
            if existing and existing[0] != request_id:
                raise Rejected('That usage result is already assigned to another request.')
            # Record an overrun honestly. Future reservations stop until reviewed.
            self.db.execute('UPDATE openrouter_spend SET state=?,actual=?,generation=? WHERE id=?',
                            ('settled', cost, generation, request_id))
