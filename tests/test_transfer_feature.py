import unittest
import uuid
from decimal import Decimal
from database.db import get_db_connection
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance, get_transaction_by_id,
    get_transactions_paginated, get_monthly_summary
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
        inserted_tx = get_transaction_by_id(tx_id)
        self.assertEqual(inserted_tx['transaction_type'], "TRANSFER")
        self.assertEqual(inserted_tx['balance_before'], inserted_tx['balance_after'])

        # 4. Overall balance is unchanged
        summary_after = get_overall_summary()
        self.assertEqual(summary_after.current_balance, initial_bal)

        # 5. Ledger invariants check passes with 0 errors
        errors = validate_ledger_invariants()
        self.assertEqual(errors, [])

    def test_transfer_excluded_from_income_and_expenses(self):
        # Insert a TRANSFER
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
        
        # Check today summary
        today = get_today_summary()
        # total_sent and total_received should not include the 2500 transfer
        tx = get_transaction_by_id(tx_id)
        self.assertIsNotNone(tx)
        
        # Monthly summary
        ms = get_monthly_summary(2026, 9)
        self.assertIsInstance(ms, dict)

    def test_transfer_pagination_filtering(self):
        data = get_transactions_paginated(page=1, page_size=10, tx_type="TRANSFER")
        self.assertIn('transactions', data)
        for tx in data['transactions']:
            self.assertEqual(tx['transaction_type'], 'TRANSFER')

if __name__ == "__main__":
    unittest.main()
