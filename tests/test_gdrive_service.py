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

    @patch('services.gdrive_service._get_gdrive_service')
    @patch('services.gdrive_service._get_or_create_subfolder', return_value='folder_backups')
    @patch('services.gdrive_service.os.path.exists', return_value=True)
    def test_upload_backup_to_drive_records_confirmed_backup(self, mock_exists, mock_folder, mock_get_svc):
        from database.db import setup_database, get_db_connection
        from services.gdrive_service import upload_backup_to_drive
        setup_database()
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '1', '2026-10-09T00:00:00Z')")
            conn.commit()

        mock_svc = MagicMock()
        mock_create = MagicMock()
        mock_create.execute.return_value = {'id': 'backup_drive_123', 'webViewLink': 'https://drive.google.com/file/d/123'}
        mock_svc.files().create.return_value = mock_create
        mock_list = MagicMock()
        mock_list.execute.return_value = {'files': [{'id': 'f1'}, {'id': 'f2'}]}
        mock_svc.files().list.return_value = mock_list
        mock_get_svc.return_value = mock_svc

        with patch('services.gdrive_service.GDRIVE_SERVICE_ACCOUNT_JSON', '{"type": "service_account"}'), \
             patch('googleapiclient.http.MediaFileUpload', MagicMock()):
            drive_id = upload_backup_to_drive('backup.json', max_retention=7)
            self.assertEqual(drive_id, 'backup_drive_123')

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings WHERE key IN ('is_dirty', 'last_confirmed_backup_at', 'last_gdrive_backup_at')")
            settings = {row['key']: row['value'] for row in cursor.fetchall()}
            self.assertEqual(settings.get('is_dirty'), '0')
            self.assertIsNotNone(settings.get('last_confirmed_backup_at'))
            self.assertIsNotNone(settings.get('last_gdrive_backup_at'))

    @patch('services.gdrive_service._get_gdrive_service')
    @patch('services.gdrive_service._get_or_create_subfolder', return_value='folder_backups')
    def test_rotate_drive_backups(self, mock_folder, mock_get_svc):
        from services.gdrive_service import rotate_drive_backups
        mock_svc = MagicMock()
        # Simulate 10 files in Backups folder
        mock_files = [{'id': f'id_{i}', 'name': f'backup_{i}.json'} for i in range(10)]
        mock_list = MagicMock()
        mock_list.execute.return_value = {'files': mock_files}
        mock_svc.files().list.return_value = mock_list
        mock_delete = MagicMock()
        mock_delete.execute.return_value = {}
        mock_svc.files().delete.return_value = mock_delete
        mock_get_svc.return_value = mock_svc

        with patch('services.gdrive_service.GDRIVE_SERVICE_ACCOUNT_JSON', '{"type": "service_account"}'):
            deleted = rotate_drive_backups(max_retention=7)
            self.assertEqual(deleted, 3)
            # Should have called delete 3 times for id_7, id_8, id_9
            self.assertEqual(mock_svc.files().delete.call_count, 3)


if __name__ == '__main__':
    unittest.main()

