import sqlite3
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from dervo_connections import Rejected
from openrouter_budget import Budget, amount
from release_profiles import stage

OWNER = ('z'*20, 'https://'+'z'*20+'.supabase.co/auth/v1', '12345678-1234-4234-8234-123456789abc')
OTHER = (*OWNER[:2], '22345678-1234-4234-8234-123456789abc')


class BudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stage(self.root, 'personal-experimental')
        self.path = self.root/'test.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.budget = Budget(self.db, resources=self.root)

    def configure(self, total='1', per_request='0.6', revision=0):
        return self.budget.configure(OWNER, total=total, per_request=per_request, revision=revision)

    def reserve(self, bound='0.6'):
        request = str(uuid4())
        self.budget.reserve(OWNER, request, bound)
        return request

    def test_explicit_limits_and_exact_arithmetic(self):
        self.assertFalse(self.budget.status(OWNER)['configured'])
        with self.assertRaises(Rejected): self.reserve()
        self.configure('0.3','0.1')
        for _ in range(3): self.reserve('0.1')
        self.assertEqual(self.budget.status(OWNER)['remainingUsd'], '0')
        with self.assertRaises(Rejected): self.reserve('0.000000001')

    def test_invalid_money_never_weakens_cap(self):
        for value in ('NaN','Infinity','-1','1e2','10001','0.001',' 1','1.000',1,True,None):
            with self.subTest(value=value), self.assertRaises(Rejected): amount(value, cap=True)
        self.assertEqual(amount('0.000000001'), 1)

    def test_existing_reservation_blocks_overspending_through_second_connection(self):
        self.configure()
        self.reserve()
        other_db = sqlite3.connect(self.path)
        self.addCleanup(other_db.close)
        second = Budget(other_db, resources=self.root)
        with self.assertRaises(Rejected): second.reserve(OWNER, str(uuid4()), '0.6')
        self.assertEqual(second.status(OWNER)['reservedUsd'], '0.6')

    def test_concurrent_reservations_serialize_before_reading_remaining_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.configure()
        barrier = Barrier(2)
        def worker():
            db = sqlite3.connect(self.path)
            try:
                budget = Budget(db, resources=self.root)
                barrier.wait(timeout=3)
                try:
                    budget.reserve(OWNER, str(uuid4()), '0.6')
                    return True
                except Rejected:
                    return False
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            self.assertEqual(sum(f.result(timeout=5) for f in futures), 1)

    def test_cancel_releases_only_unsent_reservations(self):
        self.configure()
        first = self.reserve()
        self.budget.cancel_unsent(OWNER, first)
        second = self.reserve()
        self.budget.mark_sent(OWNER, second)
        with self.assertRaises(Rejected): self.budget.cancel_unsent(OWNER, second)
        self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0.6')

    def test_uncertain_charge_retains_reservation_and_blocks_new_work(self):
        self.configure()
        request = self.reserve('0.1')
        self.budget.mark_sent(OWNER, request)
        self.budget.uncertain(OWNER, request)
        with self.assertRaises(Rejected): self.reserve('0.1')
        self.assertEqual(self.budget.status(OWNER)['uncertainRequests'], 1)
        self.budget.settle(OWNER, request, cost='0.025', generation='gen-one')
        self.assertEqual(self.budget.status(OWNER)['usedUsd'], '0.025')
        self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0')
        self.reserve('0.1')

    def test_restart_releases_unsent_and_preserves_potential_charges(self):
        self.configure()
        self.reserve('0.1')
        sent = self.reserve('0.2')
        self.budget.mark_sent(OWNER, sent)
        other_db = sqlite3.connect(self.path)
        self.addCleanup(other_db.close)
        second = Budget(other_db, resources=self.root)
        second.recover(OWNER)
        status = second.status(OWNER)
        self.assertEqual(status['reservedUsd'], '0.2')
        self.assertEqual(status['uncertainRequests'], 1)
        self.assertEqual(status['totalUsd'], '1')
        with self.assertRaises(Rejected): second.reserve(OWNER, str(uuid4()), '0.01')

    def test_replayed_request_or_receipt_cannot_double_send_or_refund(self):
        self.configure()
        request = self.reserve('0.2')
        with self.assertRaises(Rejected): self.budget.reserve(OWNER, request, '0.2')
        self.budget.mark_sent(OWNER, request)
        with self.assertRaises(Rejected): self.budget.mark_sent(OWNER, request)
        self.budget.settle(OWNER, request, cost='0.1', generation='gen-one')
        self.budget.settle(OWNER, request, cost='0.1', generation='gen-one')
        with self.assertRaises(Rejected): self.budget.settle(OWNER, request, cost='0', generation='gen-one')
        second = self.reserve('0.2')
        self.budget.mark_sent(OWNER, second)
        with self.assertRaises(Rejected): self.budget.settle(OWNER, second, cost='0', generation='gen-one')
        self.assertEqual(self.budget.status(OWNER)['usedUsd'], '0.1')
        self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0.2')

    def test_observed_overrun_is_recorded_and_blocks_further_work(self):
        self.configure()
        request = self.reserve('0.1')
        self.budget.mark_sent(OWNER, request)
        self.budget.settle(OWNER, request, cost='0.2', generation='gen-one')
        self.assertTrue(self.budget.status(OWNER)['costExceededReservation'])
        self.assertEqual(self.budget.status(OWNER)['usedUsd'], '0.2')
        with self.assertRaises(Rejected): self.reserve('0.1')

    def test_owner_cannot_read_or_change_another_owners_spending(self):
        self.configure()
        request = self.reserve()
        self.assertEqual(self.budget.status(OTHER)['reservedUsd'], '0')
        for method in (self.budget.mark_sent, self.budget.cancel_unsent, self.budget.uncertain):
            with self.assertRaises(Rejected): method(OTHER, request)
        with self.assertRaises(Rejected): self.budget.settle(OTHER, request, cost='0', generation='gen-one')

    def test_budget_edits_cannot_remove_charges_or_use_stale_revision(self):
        self.configure()
        self.reserve()
        with self.assertRaises(Rejected): self.configure('0.5','0.1',1)
        with self.assertRaises(Rejected): self.configure('2','0.6',0)
        self.configure('2','0.6',1)
        self.assertEqual(self.budget.status(OWNER)['reservedUsd'], '0.6')

    def test_send_rechecks_lowered_request_limit(self):
        self.configure()
        request = self.reserve()
        self.configure('1','0.1',1)
        with self.assertRaises(Rejected): self.budget.mark_sent(OWNER, request)
        self.budget.cancel_unsent(OWNER, request)

    def test_send_rechecks_new_uncertainty_and_cost_overrun(self):
        self.configure()
        first, second = self.reserve('0.2'), self.reserve('0.2')
        self.budget.mark_sent(OWNER, first)
        self.budget.uncertain(OWNER, first)
        with self.assertRaises(Rejected): self.budget.mark_sent(OWNER, second)
        self.budget.settle(OWNER, first, cost='0.3', generation='gen-one')
        with self.assertRaises(Rejected): self.budget.mark_sent(OWNER, second)

    def test_beta_denied_before_schema_and_after_personal_construction(self):
        stage(self.root, 'private-beta')
        empty = sqlite3.connect(':memory:')
        self.addCleanup(empty.close)
        with self.assertRaises(ValueError): Budget(empty, resources=self.root)
        self.assertEqual(empty.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 0)
        with self.assertRaises(ValueError): self.budget.status(OWNER)
        with self.assertRaises(ValueError): self.configure()

    def test_nested_transaction_cannot_commit_unrelated_workspace_changes(self):
        self.configure()
        self.db.execute('CREATE TABLE test_pending(value TEXT)')
        self.db.execute("INSERT INTO test_pending VALUES ('pending')")
        with self.assertRaises(Rejected): Budget(self.db, resources=self.root)
        with self.assertRaises(Rejected): self.reserve()
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM test_pending').fetchone()[0], 0)
