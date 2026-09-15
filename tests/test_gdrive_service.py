import unittest
from unittest.mock import patch, MagicMock
import os
from services.gdrive_service import is_gdrive_available, upload_file_to_drive, upload_receipt_to_drive

class TestGDriveService(unittest.TestCase):
    def test_gdrive_not_available_by_default(self):
        with patch('services.gdrive_service.GDRIVE_SERVICE_ACCOUNT_JSON', None), \
             patch('services.gdrive_service.GDRIVE_CLIENT_ID', None), \
             patch('services.gdrive_service.os.path.exists', return_value=False):
            self.assertFalse(is_gdrive_available())
            res = upload_file_to_drive('test.pdf')
            self.assertIsNone(res)

    @patch('services.gdrive_service._get_gdrive_service')
    @patch('services.gdrive_service._get_or_create_subfolder', return_value='folder_123')
    @patch('services.gdrive_service.os.path.exists', return_value=True)
    def test_upload_receipt_success(self, mock_exists, mock_folder, mock_get_svc):
        mock_svc = MagicMock()
        mock_create = MagicMock()
        mock_create.execute.return_value = {'id': 'file_id_999', 'webViewLink': 'https://drive.google.com/file/d/999'}
        mock_svc.files().create.return_value = mock_create
        mock_get_svc.return_value = mock_svc

        with patch('services.gdrive_service.GDRIVE_SERVICE_ACCOUNT_JSON', '{"type": "service_account"}'):
            self.assertTrue(is_gdrive_available())
            with patch('googleapiclient.http.MediaFileUpload', MagicMock()):
                res = upload_receipt_to_drive('test_receipt.jpg')
                self.assertEqual(res, 'file_id_999')

if __name__ == '__main__':
    unittest.main()
