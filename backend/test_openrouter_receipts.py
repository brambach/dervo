"""Offline HTTP and durable-ledger controls. No real key or paid request."""
from decimal import Decimal
from io import BytesIO
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from uuid import uuid4

from dervo_connections import Rejected
from openrouter_budget import Budget
from openrouter_connection import ReadOnlyClient, ConnectionError
from openrouter_receipts import reconcile
from release_profiles import stage
from test_openrouter_budget import OWNER, OTHER
from test_openrouter_connection import KEY, Response


def data(**changes):
    row = dict(id='gen-fixture', total_cost=Decimal('0.012345678901'), is_byok=False,
               cancelled=False, finish_reason='stop')
    row.update(changes)
    return row


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); stage(self.root, 'personal-experimental')
        self.db = sqlite3.connect(self.root/'spend.sqlite3'); self.addCleanup(self.db.close)
        self.budget = Budget(self.db, resources=self.root)
        self.budget.configure(OWNER, total='2', per_request='1', revision=0)
        self.request = str(uuid4()); self.budget.reserve(OWNER, self.request, '0.5')
        self.budget.mark_sent(OWNER, self.request)
        self.budget.bind_generation(OWNER, self.request, 'gen-fixture')
        self.opener = Mock(); self.client = ReadOnlyClient(self.root, opener=self.opener)

    def response(self, cost='0.012345678901', **changes):
        row = data(); row.pop('total_cost'); row.update(changes)
        response = Response({'data': row})
        response.raw = response.raw[:-2] + b', "total_cost": ' + cost.encode() + b'}}'
        self.opener.open.side_effect = None; self.opener.open.return_value = response

    def test_exact_decimal_http_receipt_settles_once_without_returning_raw_metadata(self):
        self.response(prompt=KEY, provider_name=KEY, upstream_id=KEY)
        status = reconcile(self.client, KEY, self.budget, OWNER, self.request)
        self.assertEqual(status['usedUsd'], '0.012345679')
        self.assertEqual(status['reservedUsd'], '0')
        self.assertNotIn(KEY, json.dumps(status))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/generation?id=gen-fixture')
        self.assertEqual(request.get_method(), 'GET'); self.assertIsNone(request.data)
        self.assertEqual(reconcile(self.client, KEY, self.budget, OWNER, self.request), status)

    def test_missing_delayed_partial_or_external_cost_never_releases_budget(self):
        for cost, changes in [('null', {}), ('0', {'finish_reason': None}), ('1', {'is_byok': True}),
                              ('1', {'cancelled': None}), ('1', {'id': 'other-generation'})]:
            with self.subTest(cost=cost, changes=changes):
                self.response(cost, **changes)
                with self.assertRaises(ConnectionError): reconcile(self.client, KEY, self.budget, OWNER, self.request)
                self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0.5')
        self.opener.open.side_effect = HTTPError('https://example.test/'+KEY, 404, KEY, {}, BytesIO(KEY.encode()))
        with self.assertRaises(ConnectionError) as caught: reconcile(self.client, KEY, self.budget, OWNER, self.request)
        self.assertEqual(caught.exception.code, 'usage_pending'); self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0.5')

    def test_bad_numbers_rejected_and_fractional_nanodollar_rounds_up(self):
        for cost in ['true','"0.1"','-0.1','NaN','Infinity','10000.01','1e10000']:
            self.response(cost)
            with self.assertRaises(ConnectionError): self.client.generation(KEY, 'gen-fixture')
        self.response('0.000000000000000001')
        self.assertEqual(self.client.generation(KEY, 'gen-fixture').cost_usd, '0.000000001')
        self.response('0', cancelled=True, finish_reason='error')
        self.assertEqual(self.client.generation(KEY, 'gen-fixture').cost_usd, '0.000000000')

    def test_generation_cannot_move_between_requests_or_accounts(self):
        second = str(uuid4()); self.budget.reserve(OWNER, second, '0.5'); self.budget.mark_sent(OWNER, second)
        with self.assertRaises(Rejected): self.budget.bind_generation(OWNER, second, 'gen-fixture')
        with self.assertRaises(Rejected): self.budget.bind_generation(OWNER, self.request, 'different')
        with self.assertRaises(Rejected): self.budget.settle(OWNER, self.request, cost='0', generation='different')
        with self.assertRaises(Rejected): reconcile(self.client, KEY, self.budget, OTHER, self.request)
        self.opener.open.assert_not_called()

    def test_restart_preserves_generation_and_unknown_hold_then_records_overrun(self):
        db = sqlite3.connect(self.root/'spend.sqlite3'); self.addCleanup(db.close)
        recovered = Budget(db, resources=self.root); recovered.recover(OWNER)
        self.assertEqual(recovered.receipt_generation(OWNER, self.request), 'gen-fixture')
        self.assertEqual(recovered.status(OWNER)['uncertainRequests'], 1)
        self.response('0.7'); status = reconcile(self.client, KEY, recovered, OWNER, self.request)
        self.assertEqual(status['usedUsd'], '0.7'); self.assertTrue(status['costExceededReservation'])
        with self.assertRaises(Rejected): recovered.reserve(OWNER, str(uuid4()), '0.1')

    def test_beta_profile_and_injected_generation_paths_never_access_network(self):
        for value in ['../key','gen?api_key=secret','gen/other','gen\nfixture','x'*151]:
            with self.assertRaises(ConnectionError): self.client.generation(KEY, value)
        self.opener.open.assert_not_called()
        stage(self.root, 'private-beta')
        with self.assertRaises(ValueError): self.client.generation(KEY, 'gen-fixture')
        with self.assertRaises(ValueError): reconcile(self.client, KEY, self.budget, OWNER, self.request)
        self.opener.open.assert_not_called()

    def test_missing_generation_id_stays_reserved_without_network(self):
        second = str(uuid4()); self.budget.reserve(OWNER, second, '0.5'); self.budget.mark_sent(OWNER, second)
        with self.assertRaises(Rejected): reconcile(self.client, KEY, self.budget, OWNER, second)
        self.opener.open.assert_not_called(); self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '1')

    def test_delayed_receipt_retry_reads_again_without_resending(self):
        self.budget.uncertain(OWNER, self.request)
        self.opener.open.side_effect = HTTPError('https://example.test', 404, 'missing', {}, BytesIO(b''))
        with self.assertRaises(ConnectionError):
            reconcile(self.client, KEY, self.budget, OWNER, self.request)
        self.assertEqual(self.budget.status(OWNER)['uncertainRequests'], 1)
        self.response('0.2')
        result = reconcile(self.client, KEY, self.budget, OWNER, self.request)
        self.assertEqual(result['usedUsd'], '0.2')
        self.assertEqual(result['uncertainRequests'], 0)
        for call in self.opener.open.call_args_list:
            self.assertEqual(call.args[0].get_method(), 'GET')
            self.assertIsNone(call.args[0].data)

    def test_profile_change_during_receipt_fetch_does_not_settle(self):
        self.response('0.2')
        response = self.opener.open.return_value
        def switch_profile(*args, **kwargs):
            stage(self.root, 'private-beta')
            return response
        self.opener.open.side_effect = switch_profile
        with self.assertRaises(ValueError):
            reconcile(self.client, KEY, self.budget, OWNER, self.request)
        stage(self.root, 'personal-experimental')
        self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0.5')
        self.assertEqual(self.budget.status(OWNER)['usedUsd'], '0')
