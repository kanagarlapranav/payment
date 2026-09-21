import unittest
from unittest.mock import patch, MagicMock
import os
import json
from database.models import Transaction
from utils.dates import get_current_time_in_tz
# Pre-warm pytz cache before mock_open
get_current_time_in_tz()

from ocr.gemini_vision import (
    is_gemini_available,
    extract_transaction_with_gemini,
    get_effective_gemini_api_key,
)

class TestGeminiVision(unittest.TestCase):
    def test_gemini_not_available_by_default(self):
        with patch('ocr.gemini_vision.GEMINI_API_KEY', "DISABLED"):
            self.assertFalse(is_gemini_available())
            tx, conf = extract_transaction_with_gemini('non_existent.jpg')
            self.assertIsNone(tx)
            self.assertEqual(conf, 0)

    def test_gemini_key_variations(self):
        for disabled_val in ["DISABLED", "NONE", "NULL", "none", "null", "disabled", ""]:
            with patch('ocr.gemini_vision.GEMINI_API_KEY', disabled_val):
                with patch.dict(os.environ, {"GOOGLE_API_KEY": "", "GEMINI_API_KEY": ""}):
                    self.assertEqual(get_effective_gemini_api_key(), "")

        with patch('ocr.gemini_vision.GEMINI_API_KEY', None):
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "env_google_key_123", "GEMINI_API_KEY": ""}):
                self.assertEqual(get_effective_gemini_api_key(), "env_google_key_123")

            with patch.dict(os.environ, {"GOOGLE_API_KEY": "", "GEMINI_API_KEY": "env_gemini_key_456"}):
                self.assertEqual(get_effective_gemini_api_key(), "env_gemini_key_456")

    @patch('ocr.gemini_vision.os.path.exists')
    @patch('builtins.open', unittest.mock.mock_open(read_data=b'dummy_image_bytes'))
    def test_gemini_successful_extraction(self, mock_exists):
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
                                    "person_name": "SAMPLE MERCHANT",
                                    "payment_app": "Amazon Pay",
                                    "transaction_date": "2026-09-15",
                                    "transaction_time": "7:54 PM",
                                    "bank_name": "SAMPLE BANK",
                                    "reference_number": "123456789012"
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

        with patch('ocr.gemini_vision._call_gemini_api_async', return_value=(mock_resp, None)):
            with patch('ocr.gemini_vision.GEMINI_API_KEY', 'test_key_123'):
                self.assertTrue(is_gemini_available())
                tx, conf = extract_transaction_with_gemini(
                    'dummy_receipt.jpg', ocr_text="Paid 400.00 Ref 123456789012"
                )

                self.assertIsNotNone(tx)
                self.assertEqual(tx.amount, 400.0)
                self.assertEqual(tx.transaction_type, 'SENT')
                self.assertEqual(tx.person_name, 'SAMPLE MERCHANT')
                self.assertEqual(tx.payment_app, 'Amazon Pay')
                self.assertEqual(tx.bank_name, 'SAMPLE BANK')
                self.assertEqual(tx.reference_number, '123456789012')
                self.assertGreaterEqual(conf, 90)

if __name__ == '__main__':
    unittest.main()
