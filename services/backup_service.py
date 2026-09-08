import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from config import DATA_DIR, DB_PATH, TELEGRAM_GROUP_ID, TELEGRAM_USER_ID, logger
from database.db import get_db_connection

BACKUP_JSON_PATH = DATA_DIR / 'backup_transactions.json'

def export_database_to_json(output_path: Path = None) -> dict:
    """Exports all transactions and settings to a JSON structure and saves to disk."""
    path = output_path or BACKUP_JSON_PATH
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transactions ORDER BY id ASC")
            tx_rows = [dict(row) for row in cursor.fetchall()]
            
            # Format dates/timestamps for JSON serialization
            for tx in tx_rows:
                for k, v in tx.items():
                    if isinstance(v, (datetime, )):
                        tx[k] = v.isoformat()
                    elif v is not None and not isinstance(v, (int, float, str, bool)):
                        tx[k] = str(v)
                        
            cursor.execute("SELECT key, value FROM settings")
            settings = {row['key']: row['value'] for row in cursor.fetchall()}
            
        backup_data = {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "transaction_count": len(tx_rows),
            "settings": settings,
            "transactions": tx_rows
        }
        
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
            
        logger.info(f"Exported {len(tx_rows)} transactions to JSON backup at {path}")
        return backup_data
    except Exception as e:
        logger.error(f"Error exporting database to JSON: {e}", exc_info=True)
        return {}

def import_database_from_json(input_path: Path = None, data_dict: dict = None) -> bool:
    """Imports transactions and settings from JSON into SQLite database."""
    path = input_path or BACKUP_JSON_PATH
    try:
        if data_dict is None:
            if not path.exists():
                logger.info(f"No backup JSON found at {path}")
                return False
            with open(path, 'r', encoding='utf-8') as f:
                data_dict = json.load(f)
                
        transactions = data_dict.get("transactions", [])
        settings = data_dict.get("settings", {})
        
        if not transactions:
            logger.info("Backup JSON contains 0 transactions, skipping import.")
            return False
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Map existing transactions by reference number and ID
            cursor.execute("SELECT id, reference_number FROM transactions")
            existing_by_ref = {}
            existing_ids = set()
            for row in cursor.fetchall():
                existing_ids.add(row['id'])
                if row['reference_number']:
                    existing_by_ref[row['reference_number']] = row['id']
                
            inserted_count = 0
            updated_count = 0
            for tx in transactions:
                ref = tx.get('reference_number') or ''
                tx_id = tx.get('id')
                
                # If existing by reference number, update it
                if ref and ref in existing_by_ref:
                    db_id = existing_by_ref[ref]
                    cursor.execute('''
                        UPDATE transactions SET
                            transaction_type = ?, amount = ?, person_name = ?, sender_name = ?,
                            recipient_name = ?, upi_id = ?, phone_number = ?, transaction_date = ?,
                            transaction_time = ?, payment_app = ?, bank_name = ?, bank_account = ?,
                            payment_status = ?, balance_before = ?, balance_after = ?, ocr_text = ?,
                            updated_at = ?
                        WHERE id = ?
                    ''', (
                        tx.get('transaction_type', 'RECEIVED'),
                        float(tx.get('amount', 0.0)),
                        tx.get('person_name', ''),
                        tx.get('sender_name', ''),
                        tx.get('recipient_name', ''),
                        tx.get('upi_id', ''),
                        tx.get('phone_number', ''),
                        tx.get('transaction_date'),
                        tx.get('transaction_time', ''),
                        tx.get('payment_app', ''),
                        tx.get('bank_name', ''),
                        tx.get('bank_account', ''),
                        tx.get('payment_status', 'SUCCESS'),
                        float(tx.get('balance_before', 0.0)),
                        float(tx.get('balance_after', 0.0)),
                        tx.get('ocr_text', ''),
                        tx.get('updated_at', datetime.now().isoformat()),
                        db_id
                    ))
                    updated_count += 1
                elif tx_id and tx_id in existing_ids:
                    # Update by ID
                    cursor.execute('''
                        UPDATE transactions SET
                            transaction_type = ?, amount = ?, person_name = ?, sender_name = ?,
                            recipient_name = ?, upi_id = ?, phone_number = ?, transaction_date = ?,
                            transaction_time = ?, reference_number = ?, payment_app = ?, bank_name = ?,
                            bank_account = ?, payment_status = ?, balance_before = ?, balance_after = ?,
                            ocr_text = ?, updated_at = ?
                        WHERE id = ?
                    ''', (
                        tx.get('transaction_type', 'RECEIVED'),
                        float(tx.get('amount', 0.0)),
                        tx.get('person_name', ''),
                        tx.get('sender_name', ''),
                        tx.get('recipient_name', ''),
                        tx.get('upi_id', ''),
                        tx.get('phone_number', ''),
                        tx.get('transaction_date'),
                        tx.get('transaction_time', ''),
                        ref,
                        tx.get('payment_app', ''),
                        tx.get('bank_name', ''),
                        tx.get('bank_account', ''),
                        tx.get('payment_status', 'SUCCESS'),
                        float(tx.get('balance_before', 0.0)),
                        float(tx.get('balance_after', 0.0)),
                        tx.get('ocr_text', ''),
                        tx.get('updated_at', datetime.now().isoformat()),
                        tx_id
                    ))
                    updated_count += 1
                else:
                    # Insert new record
                    cursor.execute('''
                        INSERT INTO transactions (
                            transaction_type, amount, person_name, sender_name, recipient_name,
                            upi_id, phone_number, transaction_date, transaction_time, reference_number,
                            transaction_id, payment_app, bank_name, bank_account, payment_status,
                            balance_before, balance_after, ocr_text, original_image_path,
                            telegram_message_id, telegram_chat_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        tx.get('transaction_type', 'RECEIVED'),
                        float(tx.get('amount', 0.0)),
                        tx.get('person_name', ''),
                        tx.get('sender_name', ''),
                        tx.get('recipient_name', ''),
                        tx.get('upi_id', ''),
                        tx.get('phone_number', ''),
                        tx.get('transaction_date'),
                        tx.get('transaction_time', ''),
                        ref,
                        tx.get('transaction_id', ''),
                        tx.get('payment_app', ''),
                        tx.get('bank_name', ''),
                        tx.get('bank_account', ''),
                        tx.get('payment_status', 'SUCCESS'),
                        float(tx.get('balance_before', 0.0)),
                        float(tx.get('balance_after', 0.0)),
                        tx.get('ocr_text', ''),
                        tx.get('original_image_path', ''),
                        str(tx.get('telegram_message_id', '')),
                        str(tx.get('telegram_chat_id', '')),
                        tx.get('created_at', datetime.now().isoformat()),
                        tx.get('updated_at', datetime.now().isoformat())
                    ))
                    inserted_count += 1
                    if ref:
                        existing_by_ref[ref] = cursor.lastrowid
                
            # Restore settings (like balance)
            for k, v in settings.items():
                cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
                
            conn.commit()
            
        logger.info(f"Imported database from JSON: {inserted_count} inserted, {updated_count} updated.")
        return True
    except Exception as e:
        logger.error(f"Error importing database from JSON: {e}", exc_info=True)
        return False

async def backup_to_telegram(bot, chat_id: str = None) -> bool:
    """
    Exports the current database to JSON and uploads it to Telegram as a pinned backup document.
    """
    target_chat = chat_id or TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
    if not target_chat or not bot:
        logger.warning("No Telegram chat or bot available for cloud backup.")
        return False
        
    try:
        data = export_database_to_json()
        if not BACKUP_JSON_PATH.exists():
            return False
            
        tx_count = data.get("transaction_count", 0)
        caption = f"#PAYMENT_TRACKER_BACKUP ☁️ Auto-Backup ({tx_count} records)"
        
        with open(BACKUP_JSON_PATH, 'rb') as doc_file:
            msg = await bot.send_document(
                chat_id=target_chat,
                document=doc_file,
                filename="payment_tracker_backup.json",
                caption=caption,
                disable_notification=True
            )
            
        try:
            # Pin the backup silently so it can always be retrieved on fresh boots
            await bot.pin_chat_message(
                chat_id=target_chat,
                message_id=msg.message_id,
                disable_notification=True
            )
        except Exception as pin_err:
            logger.info(f"Could not pin backup message (maybe not admin or already pinned): {pin_err}")
            
        logger.info("Successfully backed up database to Telegram cloud.")
        return True
    except Exception as e:
        logger.error(f"Failed to backup database to Telegram: {e}", exc_info=True)
        return False

async def restore_from_telegram(bot, chat_id: str = None) -> bool:
    """
    Retrieves the latest pinned backup document from Telegram and restores the database.
    """
    target_chat = chat_id or TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
    if not target_chat or not bot:
        logger.warning("No Telegram chat or bot available for cloud restore.")
        return False
        
    try:
        chat = await bot.get_chat(chat_id=target_chat)
        pinned = chat.pinned_message
        if not pinned or not pinned.document:
            logger.info("No pinned backup document found in Telegram chat.")
            # Fall back to local JSON if present
            if BACKUP_JSON_PATH.exists():
                return import_database_from_json(BACKUP_JSON_PATH)
            return False
            
        is_backup = (
            (pinned.caption and "#PAYMENT_TRACKER_BACKUP" in pinned.caption) or
            ("backup" in (pinned.document.file_name or "").lower())
        )
        if not is_backup:
            logger.info("Pinned message is not a payment tracker backup.")
            return False
            
        logger.info(f"Found pinned cloud backup: {pinned.document.file_name}. Downloading...")
        file = await bot.get_file(pinned.document.file_id)
        download_path = DATA_DIR / "temp_cloud_backup.json"
        await file.download_to_drive(custom_path=download_path)
        
        success = import_database_from_json(input_path=download_path)
        if download_path.exists():
            try:
                os.remove(download_path)
            except OSError:
                pass
        return success
    except Exception as e:
        logger.error(f"Error restoring backup from Telegram: {e}", exc_info=True)
        # Fall back to local JSON if available
        if BACKUP_JSON_PATH.exists():
            return import_database_from_json(BACKUP_JSON_PATH)
        return False
