import unittest
from unittest.mock import patch, MagicMock
import os
import json
from database.models import Transaction
from ocr.gemini_vision import is_gemini_available, extract_transaction_with_gemini

class TestGeminiVision(unittest.TestCase):
    def test_gemini_not_available_by_default(self):
        with patch('ocr.gemini_vision.GEMINI_API_KEY', "DISABLED"), patch('ocr.gemini_vision.FALLBACK_GEMINI_KEY', ""):
            self.assertFalse(is_gemini_available())
            tx, conf = extract_transaction_with_gemini('non_existent.jpg')
            self.assertIsNone(tx)
            self.assertEqual(conf, 0)


    @patch('ocr.gemini_vision.requests.post')
    @patch('ocr.gemini_vision.os.path.exists')
    @patch('builtins.open', unittest.mock.mock_open(read_data=b'dummy_image_bytes'))
    def test_gemini_successful_extraction(self, mock_exists, mock_post):
        mock_exists.return_value = True
        
        sample_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "amount": 400.0,
                                    "transaction_type": "SENT",
                                    "person_name": "Kanagarlasaiakhil",
                                    "payment_app": "Amazon Pay",
                                    "transaction_date": "2026-09-15",
                                    "transaction_time": "7:54 PM",
                                    "bank_name": "Statebankof India",
                                    "reference_number": "625827208126",
                                    "confidence": 98
                                })
                            }
                        ]
                    }
                }
            ]
        }
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_response
        mock_post.return_value = mock_resp

        with patch('ocr.gemini_vision.GEMINI_API_KEY', 'test_key_123'):
            self.assertTrue(is_gemini_available())
            tx, conf = extract_transaction_with_gemini('dummy_receipt.jpg')
            
            self.assertIsNotNone(tx)
            self.assertEqual(tx.amount, 400.0)
            self.assertEqual(tx.transaction_type, 'SENT')
            self.assertEqual(tx.person_name, 'Kanagarlasaiakhil')
            self.assertEqual(tx.payment_app, 'Amazon Pay')
            self.assertEqual(tx.bank_name, 'Statebankof India')
            self.assertEqual(tx.reference_number, '625827208126')
            self.assertGreaterEqual(conf, 90)

if __name__ == '__main__':
    unittest.main()
