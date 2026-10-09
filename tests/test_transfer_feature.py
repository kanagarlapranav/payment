import unittest
import uuid
from decimal import Decimal
from database.db import get_db_connection
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance, get_transaction_by_id,
    get_transactions_paginated, get_monthly_summary,
    get_default_workspace_id
)
from services.balance_service import (
    recalculate_all_balances, validate_ledger_invariants,
    get_today_summary, get_overall_summary
)
from utils.validation import validate_transaction_type

class TestTransferFeature(unittest.TestCase):

    def test_validate_transaction_type(self):
        self.assertEqual(validate_transaction_type("TRANSFER"), "TRANSFER")
        self.assertEqual(validate_transaction_type("transfer"), "TRANSFER")
        self.assertEqual(validate_transaction_type("SENT"), "SENT")
        self.assertEqual(validate_transaction_type("RECEIVED"), "RECEIVED")
        with self.assertRaises(ValueError):
            validate_transaction_type("INVALID_TYPE")

    def test_transfer_does_not_alter_consolidated_balance(self):
        # 1. Get starting balance
        summary_before = get_overall_summary()
        initial_bal = summary_before.current_balance

        # 2. Insert a TRANSFER transaction of ₹5,000
        ref = f"TRF{uuid.uuid4().hex[:8].upper()}"
        t = Transaction(
            amount=5000.0,
            transaction_type="TRANSFER",
            person_name="Self Transfer - HDFC to Cash",
            sender_name="HDFC Bank",
            recipient_name="Cash Wallet",
            category="Transfer",
            transaction_date="2026-09-21",
            reference_number=ref
        )
        tx_id = insert_transaction_with_balance(t)
        self.assertIsNotNone(tx_id)

        # 3. Verify row has balance_after == balance_before
        inserted_tx = get_transaction_by_id(tx_id, workspace_id=get_default_workspace_id())
        self.assertEqual(inserted_tx['transaction_type'], "TRANSFER")
        self.assertEqual(inserted_tx['balance_before'], inserted_tx['balance_after'])

        # 4. Overall balance is unchanged
        summary_after = get_overall_summary()
        self.assertEqual(summary_after.current_balance, initial_bal)

        # 5. Ledger invariants check passes with 0 errors
        errors = validate_ledger_invariants()
        self.assertEqual(errors, [])

    def test_transfer_excluded_from_income_and_expenses(self):
        # 1. Capture base monthly summary
        ms_before = get_monthly_summary(2026, 9)
        sent_before = ms_before['total_sent']
        recv_before = ms_before['total_received']

        # 2. Insert a TRANSFER of 2500.0
        ref = f"TRF{uuid.uuid4().hex[:8].upper()}"
        t = Transaction(
            amount=2500.0,
            transaction_type="TRANSFER",
            person_name="Transfer to Savings",
            category="Transfer",
            transaction_date="2026-09-21",
            reference_number=ref
        )
        tx_id = insert_transaction_with_balance(t)
        self.assertIsNotNone(tx_id)

        # 3. Assert monthly summary total_sent and total_received completely exclude the 2500.0 transfer
        ms_after = get_monthly_summary(2026, 9)
        self.assertEqual(ms_after['total_sent'], sent_before)
        self.assertEqual(ms_after['total_received'], recv_before)

        # 4. Insert an actual expense (SENT) to verify legitimate expenses are counted
        t_expense = Transaction(
            amount=150.0,
            transaction_type="SENT",
            person_name="Coffee",
            transaction_date="2026-09-21"
        )
        insert_transaction_with_balance(t_expense)

        ms_after_expense = get_monthly_summary(2026, 9)
        self.assertEqual(ms_after_expense['total_sent'], sent_before + 150.0)
        self.assertEqual(ms_after_expense['total_received'], recv_before)

    def test_transfer_pagination_filtering(self):
        data = get_transactions_paginated(page=1, page_size=10, tx_type="TRANSFER")
        self.assertIn('transactions', data)
        for tx in data['transactions']:
            self.assertEqual(tx['transaction_type'], 'TRANSFER')

if __name__ == "__main__":
    unittest.main()
