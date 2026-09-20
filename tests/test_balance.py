import unittest
from unittest.mock import patch
from database.models import Transaction
from services.balance_service import update_balance_for_transaction

import os
from config import DB_PATH
from services.backup_service import BACKUP_JSON_PATH

class TestBalance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_backup = None
        cls.json_backup = None
        if os.path.exists(DB_PATH):
            with open(DB_PATH, 'rb') as f:
                cls.db_backup = f.read()
        if os.path.exists(BACKUP_JSON_PATH):
            with open(BACKUP_JSON_PATH, 'rb') as f:
                cls.json_backup = f.read()

    @classmethod
    def tearDownClass(cls):
        if cls.db_backup is not None:
            with open(DB_PATH, 'wb') as f:
                f.write(cls.db_backup)
        if cls.json_backup is not None:
            with open(BACKUP_JSON_PATH, 'wb') as f:
                f.write(cls.json_backup)

    def test_balance_calculation(self):
        current_balance = 50000.0
        
        def mock_get_balance():
            return current_balance
            
        def mock_set_balance(new_b):
            nonlocal current_balance
            current_balance = new_b
            
        with patch('services.balance_service.get_balance_setting', side_effect=mock_get_balance), \
             patch('services.balance_service.update_balance_setting', side_effect=mock_set_balance):
            
            # Test SENT
            t_sent = Transaction(transaction_type='SENT', amount=5000)
            t_sent = update_balance_for_transaction(t_sent)
            self.assertEqual(t_sent.balance_before, 50000.0)
            self.assertEqual(t_sent.balance_after, 45000.0)
            self.assertEqual(current_balance, 45000.0)
            
            # Test RECEIVED
            t_recv = Transaction(transaction_type='RECEIVED', amount=3000)
            t_recv = update_balance_for_transaction(t_recv)
            self.assertEqual(t_recv.balance_before, 45000.0)
            self.assertEqual(t_recv.balance_after, 48000.0)
            self.assertEqual(current_balance, 48000.0)

    def test_backup_roundtrip(self):
        from services.backup_service import export_database_to_json, import_database_from_json
        data = export_database_to_json()
        self.assertGreaterEqual(data.get("transaction_count", 0), 4)
        success = import_database_from_json(data_dict=data)
        self.assertTrue(success)

if __name__ == '__main__':
    unittest.main()
