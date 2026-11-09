import unittest
import uuid
from decimal import Decimal
from database.db import setup_database, get_db_connection
from database.models import Transaction
from database.queries import insert_transaction_with_balance, get_default_workspace_id
from services.backup_service import (
    compute_canonical_checksum,
    verify_backup_payload,
    import_database_from_json,
    export_database_to_json,
)

class TestBackupV3ChecksumCoverage(unittest.TestCase):
    def setUp(self):
        setup_database()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM recurring_payments")
            conn.execute("DELETE FROM monthly_reviews")
            conn.commit()

    def test_v3_tampered_recurring_payment_rejected(self):
        """Modifying a recurring-payment amount in Format v3 without updating checksum must be rejected."""
        uid = uuid.uuid4().hex
        payload = {
            "version": 3,
            "format_version": "v3",
            "revision": 5,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 100.0,
                "person_name": "Supplier",
                "transaction_date": "2026-10-10",
            }],
            "recurring_payments": [{
                "payee_name": "Internet Provider",
                "amount": 999.0,
                "category": "Bills & Utilities",
                "transaction_type": "SENT",
                "frequency": "MONTHLY",
                "start_date": "2026-01-01",
                "status": "ACTIVE",
            }],
            "monthly_reviews": []
        }
        payload["checksum"] = compute_canonical_checksum(payload)

        # 1. Valid payload must pass verification
        valid, msg = verify_backup_payload(payload)
        self.assertTrue(valid, msg)

        # 2. Tamper with recurring payment amount
        tampered = dict(payload)
        tampered["recurring_payments"] = [{
            "payee_name": "Internet Provider",
            "amount": 1.0,  # Tampered from 999.0 to 1.0
            "category": "Bills & Utilities",
            "transaction_type": "SENT",
            "frequency": "MONTHLY",
            "start_date": "2026-01-01",
            "status": "ACTIVE",
        }]

        # Checksum must catch the tampering
        t_valid, t_msg = verify_backup_payload(tampered)
        self.assertFalse(t_valid, "Format v3 must reject tampered recurring_payments!")
        self.assertIn("checksum", t_msg.lower())

        # Import must reject tampered payload
        res = import_database_from_json(data_dict=tampered)
        self.assertFalse(res.get("success"))

    def test_v3_tampered_monthly_review_rejected(self):
        """Modifying a monthly-review total in Format v3 without updating checksum must be rejected."""
        uid = uuid.uuid4().hex
        payload = {
            "version": 3,
            "format_version": "v3",
            "revision": 6,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "RECEIVED",
                "amount": 5000.0,
                "person_name": "Employer",
                "transaction_date": "2026-10-10",
            }],
            "recurring_payments": [],
            "monthly_reviews": [{
                "year": 2026,
                "month": 10,
                "total_income": 5000.0,
                "total_expense": 1000.0,
                "net_savings": 4000.0,
                "savings_rate_pct": 80.0,
                "is_closed": 1
            }]
        }
        payload["checksum"] = compute_canonical_checksum(payload)

        # 1. Valid payload must pass
        valid, msg = verify_backup_payload(payload)
        self.assertTrue(valid, msg)

        # 2. Tamper with monthly review net_savings
        tampered = dict(payload)
        tampered["monthly_reviews"] = [{
            "year": 2026,
            "month": 10,
            "total_income": 5000.0,
            "total_expense": 1000.0,
            "net_savings": 99999.0,  # Tampered
            "savings_rate_pct": 80.0,
            "is_closed": 1
        }]

        t_valid, t_msg = verify_backup_payload(tampered)
        self.assertFalse(t_valid, "Format v3 must reject tampered monthly_reviews!")
        self.assertIn("checksum", t_msg.lower())

        res = import_database_from_json(data_dict=tampered)
        self.assertFalse(res.get("success"))

    def test_legacy_v2_backup_still_imports_via_legacy_path(self):
        """A legitimate legacy Format v2 backup still verifies and imports successfully via the legacy path."""
        uid = uuid.uuid4().hex
        legacy_v2 = {
            "version": 2,
            "revision": 2,
            "settings": {},
            "custom_menu_items": [],
            "budgets": [],
            "transactions": [{
                "uid": uid,
                "transaction_type": "SENT",
                "amount": 75.0,
                "person_name": "Legacy Store",
                "transaction_date": "2026-10-08",
            }],
            "recurring_payments": [{
                "payee_name": "Old Sub",
                "amount": 50.0,
            }],
            "monthly_reviews": []
        }
        # In legacy v2, compute_canonical_checksum did not include recurring_payments
        legacy_v2["checksum"] = compute_canonical_checksum(legacy_v2)

        # Verification must succeed via legacy path
        valid, msg = verify_backup_payload(legacy_v2)
        self.assertTrue(valid, f"Legacy v2 backup should still be valid: {msg}")

        # Import must succeed
        res = import_database_from_json(data_dict=legacy_v2)
        self.assertTrue(res.get("success"), f"Legacy v2 import failed: {res.get('error')}")


if __name__ == '__main__':
    unittest.main()

