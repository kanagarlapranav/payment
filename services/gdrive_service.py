import os
import json
import logging
from pathlib import Path
from config import (
    GDRIVE_FOLDER_ID, GDRIVE_SERVICE_ACCOUNT_JSON, GDRIVE_CLIENT_ID,
    GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN, logger
)

def is_gdrive_available() -> bool:
    """Checks if Google Drive credentials are configured."""
    return bool(
        GDRIVE_SERVICE_ACCOUNT_JSON or 
        (GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET and GDRIVE_REFRESH_TOKEN) or
        os.path.exists("credentials.json")
    )

def _get_gdrive_service():
    """Builds and returns an authenticated Google Drive API v3 resource."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account, credentials

        # 1. Check Service Account from JSON string or file
        if GDRIVE_SERVICE_ACCOUNT_JSON:
            if os.path.exists(GDRIVE_SERVICE_ACCOUNT_JSON):
                creds = service_account.Credentials.from_service_account_file(
                    GDRIVE_SERVICE_ACCOUNT_JSON,
                    scopes=['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
                )
                return build('drive', 'v3', credentials=creds)
            else:
                try:
                    info = json.loads(GDRIVE_SERVICE_ACCOUNT_JSON)
                    creds = service_account.Credentials.from_service_account_info(
                        info,
                        scopes=['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
                    )
                    return build('drive', 'v3', credentials=creds)
                except Exception as json_err:
                    logger.warning(f"Could not parse GDRIVE_SERVICE_ACCOUNT_JSON: {json_err}")

        # 2. Check local credentials.json
        if os.path.exists("credentials.json"):
            creds = service_account.Credentials.from_service_account_file(
                "credentials.json",
                scopes=['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
            )
            return build('drive', 'v3', credentials=creds)

        # 3. Check OAuth Refresh Token
        if GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET and GDRIVE_REFRESH_TOKEN:
            creds = credentials.Credentials(
                token=None,
                refresh_token=GDRIVE_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GDRIVE_CLIENT_ID,
                client_secret=GDRIVE_CLIENT_SECRET,
                scopes=['https://www.googleapis.com/auth/drive.file']
            )
            return build('drive', 'v3', credentials=creds)

    except Exception as e:
        logger.warning(f"Google Drive authentication failed: {e}")

    return None

def _get_or_create_subfolder(service, folder_name: str, parent_id: str = None) -> str:
    """Finds or creates a subfolder within Google Drive."""
    try:
        q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
            
        results = service.files().list(q=q, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']

        # Create folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')
    except Exception as e:
        logger.error(f"Error creating folder {folder_name} on Google Drive: {e}")
        return parent_id or GDRIVE_FOLDER_ID

def upload_file_to_drive(file_path: str, destination_folder_name: str = "General") -> str | None:
    """
    Uploads a local file to Google Drive under the specified folder.
    Returns the uploaded file's Google Drive ID or None.
    """
    if not is_gdrive_available():
        return None

    if not os.path.exists(file_path):
        logger.warning(f"File not found for Drive upload: {file_path}")
        return None

    try:
        from googleapiclient.http import MediaFileUpload
        service = _get_gdrive_service()
        if not service:
            return None

        # Resolve destination folder ID
        parent_id = GDRIVE_FOLDER_ID
        if destination_folder_name:
            target_folder_id = _get_or_create_subfolder(service, destination_folder_name, parent_id=parent_id)
        else:
            target_folder_id = parent_id

        file_name = os.path.basename(file_path)
        file_metadata = {'name': file_name}
        if target_folder_id:
            file_metadata['parents'] = [target_folder_id]

        media = MediaFileUpload(file_path, resumable=True)
        uploaded = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        
        drive_id = uploaded.get('id')
        logger.info(f"Successfully uploaded {file_name} to Google Drive (ID: {drive_id})")
        return drive_id
    except Exception as e:
        logger.error(f"Google Drive upload failed for {file_path}: {e}", exc_info=True)
        return None

def upload_receipt_to_drive(file_path: str) -> str | None:
    """Uploads a payment receipt to Google Drive."""
    return upload_file_to_drive(file_path, destination_folder_name="Receipts")

def upload_statement_to_drive(file_path: str) -> str | None:
    """Uploads a PDF or Excel statement report to Google Drive."""
    return upload_file_to_drive(file_path, destination_folder_name="Statements")

def upload_backup_to_drive(file_path: str) -> str | None:
    """Uploads a JSON database backup snapshot to Google Drive."""
    return upload_file_to_drive(file_path, destination_folder_name="Backups")
