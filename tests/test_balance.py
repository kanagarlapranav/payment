import unittest
from unittest.mock import patch
from database.models import Transaction
from services.balance_service import update_balance_for_transaction

class TestBalance(unittest.TestCase):

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
