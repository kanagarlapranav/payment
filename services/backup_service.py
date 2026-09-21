import os
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from config import DATA_DIR, DB_PATH, TELEGRAM_GROUP_ID, TELEGRAM_USER_ID, logger
from database.db import get_db_connection, LEDGER_LOCK
from utils.validation import (
    parse_decimal_amount,
    validate_transaction_type,
    validate_uid,
    validate_string_length,
)

BACKUP_JSON_PATH = DATA_DIR / 'backup_transactions.json'

def export_database_to_json(output_path: Path = None) -> dict:
    """
    Exports all transactions (including tombstones), custom menu items, and settings
    to a versioned JSON structure (Format v2) and saves to disk under LEDGER_LOCK.
    Refuses to overwrite existing backup if the database is empty (Zero-Data-Loss protection).
    """
    path = output_path or BACKUP_JSON_PATH
    with LEDGER_LOCK:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # 1. Total row count check (Zero-Data-Loss safety)
                cursor.execute("SELECT COUNT(*) FROM transactions")
                total_rows = cursor.fetchone()[0]
                if total_rows == 0:
                    logger.warning("Database contains 0 transactions. Refusing to overwrite backup with empty database.")
                    return {}

                cursor.execute("SELECT * FROM transactions ORDER BY id ASC")
                tx_rows = [dict(row) for row in cursor.fetchall()]
                
                # Format dates/timestamps for JSON serialization
                for tx in tx_rows:
                    for k, v in tx.items():
                        if isinstance(v, (datetime, )):
                            tx[k] = v.isoformat()
                        elif v is not None and not isinstance(v, (int, float, str, bool)):
                            tx[k] = str(v)
                            
                # Fetch settings and increment revision
                cursor.execute("SELECT key, value FROM settings")
                settings = {row['key']: row['value'] for row in cursor.fetchall()}
                rev = int(settings.get('backup_revision', '1')) + 1
                cursor.execute("UPDATE settings SET value = ? WHERE key = 'backup_revision'", (str(rev),))
                settings['backup_revision'] = str(rev)

                # Fetch custom cafeteria menu dishes
                cursor.execute("SELECT name, price, category, is_veg FROM custom_menu_items")
                menu_items = [dict(row) for row in cursor.fetchall()]

                # Fetch live transactions count and derived balance
                cursor.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL")
                live_count = cursor.fetchone()[0]
                cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                bal_row = cursor.fetchone()
                current_balance = float(bal_row['value']) if bal_row else 0.0

            # Build payload for checksum
            payload_for_hash = {
                "version": 2,
                "revision": rev,
                "transactions": tx_rows,
                "custom_menu_items": menu_items,
                "settings": settings
            }
            canonical_str = json.dumps(payload_for_hash, sort_keys=True, ensure_ascii=False)
            checksum = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

            backup_data = {
                "version": 2,
                "revision": rev,
                "checksum": checksum,
                "exported_at": datetime.now().isoformat(),
                "transaction_count": len(tx_rows),
                "live_count": live_count,
                "balance": current_balance,
                "settings": settings,
                "custom_menu_items": menu_items,
                "transactions": tx_rows
            }
            
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)
                
            logger.info(f"Exported {len(tx_rows)} transactions (v2, rev {rev}, {live_count} live) to JSON backup at {path}")
            return backup_data
        except Exception as e:
            logger.error(f"Error exporting database to JSON: {e}", exc_info=True)
            return {}

def import_database_from_json(input_path: Path = None, data_dict: dict = None) -> dict:
    """
    Imports transactions, custom menu items, and settings from JSON into SQLite database under LEDGER_LOCK.
    Performs an idempotent UPSERT by permanent `uid` (fallback `id` for v1 backups).
    Validates amounts, transaction types, UIDs, and strings to prevent corruption.
    Never deletes local rows just because they are absent from backup.
    Returns a dict with execution details: {'success': bool, 'inserted': int, 'updated': int, 'total': int}.
    """
    path = input_path or BACKUP_JSON_PATH
    with LEDGER_LOCK:
        try:
            if data_dict is None:
                if not path.exists():
                    logger.info(f"No backup JSON found at {path}")
                    return {'success': False, 'error': 'File not found'}
                with open(path, 'r', encoding='utf-8') as f:
                    data_dict = json.load(f)
                    
            transactions = data_dict.get("transactions", [])
            settings = data_dict.get("settings", {})
            custom_menu_items = data_dict.get("custom_menu_items", [])
            version = data_dict.get("version", 1)
            
            if not transactions:
                logger.info("Backup JSON contains 0 transactions, skipping import.")
                return {'success': False, 'error': 'No transactions in backup'}
                
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Map existing transactions by permanent uid and by id
                cursor.execute("SELECT id, uid, updated_at, deleted_at FROM transactions")
                existing_by_uid = {}
                existing_by_id = {}
                for row in cursor.fetchall():
                    existing_by_id[row['id']] = dict(row)
                    if row['uid']:
                        existing_by_uid[row['uid']] = dict(row)
                    
                inserted_count = 0
                updated_count = 0
                for tx in transactions:
                    # Validate amount
                    try:
                        amt_val = float(parse_decimal_amount(tx.get('amount', 0.0), allow_zero=False))
                    except Exception as amt_err:
                        logger.warning(f"Skipping corrupt transaction during backup import: {amt_err} ({tx})")
                        continue

                    # Validate transaction_type
                    try:
                        tt_val = validate_transaction_type(tx.get('transaction_type', 'RECEIVED'))
                    except Exception:
                        tt_val = 'RECEIVED'

                    # Validate UID
                    raw_uid = tx.get('uid') or ''
                    if raw_uid:
                        try:
                            tx_uid = validate_uid(raw_uid)
                        except Exception:
                            tx_uid = ''
                    else:
                        tx_uid = ''

                    p_name = validate_string_length(tx.get('person_name', ''), max_length=120)
                    s_name = validate_string_length(tx.get('sender_name', ''), max_length=120)
                    r_name = validate_string_length(tx.get('recipient_name', ''), max_length=120)
                    ref_no = validate_string_length(tx.get('reference_number', ''), max_length=100)
                    cat_val = validate_string_length(tx.get('category', 'General'), max_length=100) or 'General'

                    try:
                        bal_before = float(parse_decimal_amount(tx.get('balance_before', 0.0), allow_zero=True))
                    except Exception:
                        bal_before = 0.0

                    try:
                        bal_after = float(parse_decimal_amount(tx.get('balance_after', 0.0), allow_zero=True))
                    except Exception:
                        bal_after = 0.0

                    tx_id = tx.get('id')
                    incoming_updated = tx.get('updated_at') or ''
                    
                    # Check if matching record exists by UID or ID
                    existing = None
                    if tx_uid and tx_uid in existing_by_uid:
                        existing = existing_by_uid[tx_uid]
                    elif tx_id and tx_id in existing_by_id:
                        existing = existing_by_id[tx_id]

                    if existing:
                        db_id = existing['id']
                        cursor.execute('''
                            UPDATE transactions SET
                                transaction_type = ?, amount = ?, person_name = ?, sender_name = ?,
                                recipient_name = ?, upi_id = ?, phone_number = ?, transaction_date = ?,
                                transaction_time = ?, reference_number = ?, transaction_id = ?,
                                payment_app = ?, bank_name = ?, bank_account = ?, payment_status = ?,
                                category = ?, balance_before = ?, balance_after = ?, ocr_text = ?,
                                original_image_path = ?, telegram_message_id = ?, telegram_chat_id = ?,
                                uid = ?, deleted_at = ?, updated_at = ?
                            WHERE id = ?
                        ''', (
                            tt_val,
                            amt_val,
                            p_name,
                            s_name,
                            r_name,
                            tx.get('upi_id', ''),
                            tx.get('phone_number', ''),
                            tx.get('transaction_date'),
                            tx.get('transaction_time', ''),
                            ref_no,
                            tx.get('transaction_id', ''),
                            tx.get('payment_app', ''),
                            tx.get('bank_name', ''),
                            tx.get('bank_account', ''),
                            tx.get('payment_status', 'SUCCESS'),
                            cat_val,
                            bal_before,
                            bal_after,
                            tx.get('ocr_text', ''),
                            tx.get('original_image_path', ''),
                            str(tx.get('telegram_message_id', '')),
                            str(tx.get('telegram_chat_id', '')),
                            tx_uid or existing.get('uid'),
                            tx.get('deleted_at'),
                            incoming_updated or datetime.now().isoformat(),
                            db_id
                        ))
                        updated_count += 1
                    else:
                        # Insert new record (including tombstones if deleted_at is set)
                        cursor.execute('''
                            INSERT INTO transactions (
                                id, transaction_type, amount, person_name, sender_name, recipient_name,
                                upi_id, phone_number, transaction_date, transaction_time, reference_number,
                                transaction_id, payment_app, bank_name, bank_account, payment_status,
                                category, balance_before, balance_after, ocr_text, original_image_path,
                                telegram_message_id, telegram_chat_id, uid, deleted_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            tx_id,
                            tt_val,
                            amt_val,
                            p_name,
                            s_name,
                            r_name,
                            tx.get('upi_id', ''),
                            tx.get('phone_number', ''),
                            tx.get('transaction_date'),
                            tx.get('transaction_time', ''),
                            ref_no,
                            tx.get('transaction_id', ''),
                            tx.get('payment_app', ''),
                            tx.get('bank_name', ''),
                            tx.get('bank_account', ''),
                            tx.get('payment_status', 'SUCCESS'),
                            cat_val,
                            bal_before,
                            bal_after,
                            tx.get('ocr_text', ''),
                            tx.get('original_image_path', ''),
                            str(tx.get('telegram_message_id', '')),
                            str(tx.get('telegram_chat_id', '')),
                            tx_uid,
                            tx.get('deleted_at'),
                            tx.get('created_at', datetime.now().isoformat()),
                            incoming_updated or datetime.now().isoformat()
                        ))
                        inserted_count += 1
                        new_id = cursor.lastrowid
                        if tx_uid:
                            existing_by_uid[tx_uid] = {'id': new_id, 'uid': tx_uid}
                        if new_id:
                            existing_by_id[new_id] = {'id': new_id, 'uid': tx_uid}
                    
                # Restore custom cafeteria menu dishes
                for dish in custom_menu_items:
                    try:
                        dish_name = validate_string_length(dish.get('name', ''), max_length=100, field_name="Dish name", required=True)
                        dish_price = float(parse_decimal_amount(dish.get('price', 0.0), allow_zero=False))
                        dish_cat = validate_string_length(dish.get('category', 'Snacks & Tea'), max_length=50) or "Snacks & Tea"
                        cursor.execute('''
                            INSERT INTO custom_menu_items (name, price, category, is_veg)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(name) DO UPDATE SET
                                price = excluded.price,
                                category = excluded.category,
                                is_veg = excluded.is_veg
                        ''', (dish_name, dish_price, dish_cat, int(dish.get('is_veg', 1))))
                    except Exception as dish_err:
                        logger.warning(f"Skipping corrupt menu dish in backup import: {dish_err}")

                # Restore settings (like balance, revision, budgets)
                for k, v in settings.items():
                    if k in ('initial_balance', 'current_balance', 'monthly_budget'):
                        try:
                            v = str(float(parse_decimal_amount(v, allow_zero=True)))
                        except Exception:
                            pass
                    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
                    
                conn.commit()

            # Recalculate balance chain over live rows and verify
            try:
                from services.balance_service import recalculate_all_balances
                derived_bal = recalculate_all_balances()
                stored_bal = data_dict.get('balance')
                if stored_bal is not None and abs(derived_bal - float(stored_bal)) > 0.01:
                    logger.warning(f"Balance verification notice: derived {derived_bal} vs stored {stored_bal}")
            except Exception as bal_err:
                logger.debug(f"Post-import balance recalculation notice: {bal_err}")
            logger.info(f"Imported database from JSON: {inserted_count} inserted, {updated_count} updated.")
            return {
                'success': True,
                'inserted': inserted_count,
                'updated': updated_count,
                'total': len(transactions)
            }
        except Exception as e:
            logger.error(f"Error importing database from JSON: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

async def backup_to_telegram(bot, chat_id: str = None) -> bool:
    """
    Exports the current database to JSON (Format v2) and uploads it to Telegram as a pinned backup document.
    Never uploads if database contains 0 transactions.
    """
    target_chat = chat_id or TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
    if not target_chat or not bot:
        logger.warning("No Telegram chat or bot available for cloud backup.")
        return False
        
    try:
        data = export_database_to_json()
        if not data or not BACKUP_JSON_PATH.exists():
            return False
            
        tx_count = data.get("transaction_count", 0)
        live_count = data.get("live_count", tx_count)
        rev = data.get("revision", 1)
        if tx_count == 0:
            logger.warning("Skipping Telegram cloud backup: 0 transactions.")
            return False

        caption = f"#PAYMENT_TRACKER_BACKUP_V2 ☁️ Auto-Backup Rev {rev} ({tx_count} records, {live_count} live)"

        with open(BACKUP_JSON_PATH, 'rb') as doc_file:
            msg = await bot.send_document(
                chat_id=target_chat,
                document=doc_file,
                filename="payment_tracker_backup.json",
                caption=caption,
                disable_notification=True
            )
            
        try:
            # Pin the latest backup silently so it can always be retrieved on fresh boots
            await bot.pin_chat_message(
                chat_id=target_chat,
                message_id=msg.message_id,
                disable_notification=True
            )
        except Exception as pin_err:
            logger.info(f"Could not pin backup message (maybe not admin or already pinned): {pin_err}")
            
        logger.info(f"Successfully backed up database (v2, rev {rev}) to Telegram cloud.")
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
            if BACKUP_JSON_PATH.exists():
                res = import_database_from_json(BACKUP_JSON_PATH)
                return bool(res.get('success'))
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

        res = import_database_from_json(input_path=download_path)
        if download_path.exists():
            try:
                os.remove(download_path)
            except OSError:
                pass
        return bool(res.get('success'))
    except Exception as e:
        logger.error(f"Error restoring backup from Telegram: {e}", exc_info=True)
        if BACKUP_JSON_PATH.exists():
            res = import_database_from_json(BACKUP_JSON_PATH)
            return bool(res.get('success'))
        return False
