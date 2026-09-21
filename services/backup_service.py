import os
import re
import json
import sqlite3
import hashlib
import threading
import uuid
import asyncio
from decimal import Decimal
from pathlib import Path
from datetime import datetime
from config import DATA_DIR, DB_PATH, TELEGRAM_GROUP_ID, TELEGRAM_USER_ID, logger
from database.db import get_db_connection, LEDGER_LOCK
from utils.dates import utc_now_iso, build_occurred_at, parse_utc_iso
from utils.validation import (
    parse_decimal_amount,
    validate_transaction_type,
    validate_uid,
    validate_string_length,
    CENT,
)

BACKUP_JSON_PATH = DATA_DIR / 'backup_transactions.json'
EXPORT_LOCK = threading.RLock()

def export_database_to_json(output_path: Path = None) -> dict:
    """
    Exports all transactions (including tombstones), custom menu items, budgets, and settings
    to a versioned JSON structure (Format v2) and saves to disk atomically under EXPORT_LOCK and LEDGER_LOCK.
    Refuses to overwrite existing backup if the database has 0 transactions (Zero-Data-Loss protection).
    Does NOT increment revision on export (revision increases per committed mutation).
    """
    path = output_path or BACKUP_JSON_PATH
    with EXPORT_LOCK:
        with LEDGER_LOCK:
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    
                    # Check database initialization and backup blocked status
                    cursor.execute("SELECT value FROM settings WHERE key = 'database_initialized'")
                    i_row = cursor.fetchone()
                    is_initialized = bool(i_row and i_row['value'] in ('1', 'true', 'True'))

                    cursor.execute("SELECT value FROM settings WHERE key = 'backup_blocked'")
                    b_row = cursor.fetchone()
                    is_blocked = bool(b_row and b_row['value'] in ('1', 'true', 'True'))

                    cursor.execute("SELECT COUNT(*) FROM transactions")
                    total_rows = cursor.fetchone()[0]
                    
                    if not is_initialized and total_rows == 0:
                        logger.warning("Database is uninitialized with 0 transactions. Refusing backup export to prevent overwriting cloud state before restore.")
                        return {}

                    if is_blocked:
                        logger.warning("Backup is currently blocked (backup_blocked=1). Refusing backup export.")
                        return {}

                    cursor.execute("SELECT * FROM transactions ORDER BY occurred_at ASC, created_at ASC, id ASC")
                    tx_rows = [dict(row) for row in cursor.fetchall()]
                    
                    # Format dates/timestamps for JSON serialization
                    for tx in tx_rows:
                        for k, v in tx.items():
                            if isinstance(v, (datetime, )):
                                tx[k] = v.isoformat()
                            elif v is not None and not isinstance(v, (int, float, str, bool)):
                                tx[k] = str(v)
                                
                    # Fetch settings (do NOT increment revision on export)
                    cursor.execute("SELECT key, value FROM settings")
                    settings = {row['key']: row['value'] for row in cursor.fetchall()}
                    rev = int(settings.get('backup_revision', '1'))

                    # Ensure database_id exists
                    db_id = settings.get('database_id')
                    now_utc = utc_now_iso()
                    if not db_id:
                        db_id = str(uuid.uuid4())
                        cursor.execute(
                            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_id', ?, ?)",
                            (db_id, now_utc)
                        )
                        settings['database_id'] = db_id

                    # Fetch custom cafeteria menu dishes
                    cursor.execute("SELECT name, price, category, is_veg FROM custom_menu_items ORDER BY id ASC")
                    menu_items = [dict(row) for row in cursor.fetchall()]

                    # Fetch budgets if table exists
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='budgets'")
                    if cursor.fetchone():
                        cursor.execute("SELECT * FROM budgets")
                        budgets = [dict(row) for row in cursor.fetchall()]
                    else:
                        budgets = []

                    # Fetch live transactions count and derived balance
                    cursor.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL")
                    live_count = cursor.fetchone()[0]
                    cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                    bal_row = cursor.fetchone()
                    current_balance = float(bal_row['value']) if bal_row else 0.0

                    # Record local backup timestamp in settings
                    cursor.execute(
                        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_local_backup_at', ?, ?)",
                        (now_utc, now_utc)
                    )
                    settings['last_local_backup_at'] = now_utc
                    conn.commit()

                # Build payload for canonical checksum
                payload_for_hash = {
                    "version": 2,
                    "revision": rev,
                    "settings": settings,
                    "custom_menu_items": menu_items,
                    "budgets": budgets,
                    "transactions": tx_rows,
                }
                canonical_str = json.dumps(
                    payload_for_hash,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(',', ':')
                )
                checksum = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

                backup_data = {
                    "version": 2,
                    "database_id": db_id,
                    "revision": rev,
                    "exported_at": now_utc,
                    "transaction_count": len(tx_rows),
                    "live_count": live_count,
                    "empty_ledger": (live_count == 0),
                    "balance": current_balance,
                    "settings": settings,
                    "custom_menu_items": menu_items,
                    "budgets": budgets,
                    "transactions": tx_rows,
                    "checksum": checksum,
                }
                
                # Atomic file write: write to .tmp file, flush, fsync, then replace
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}_{threading.get_ident()}")
                try:
                    with open(tmp_path, 'w', encoding='utf-8') as f:
                        json.dump(backup_data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    for attempt in range(10):
                        try:
                            tmp_path.replace(path)
                            break
                        except PermissionError:
                            if attempt == 9:
                                raise
                            import time
                            time.sleep(0.02 * (attempt + 1))
                finally:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    
                logger.info(f"Exported {len(tx_rows)} transactions (v2, rev {rev}, {live_count} live, empty_ledger={live_count == 0}) to JSON backup at {path}")
                return backup_data
            except Exception as e:
                logger.error(f"Error exporting database to JSON: {e}", exc_info=True)
                return {}

def compute_canonical_checksum(data_dict: dict) -> str:
    """
    Computes SHA-256 over canonical JSON (sort_keys=True, ensure_ascii=False, compact separators)
    of version, revision, settings, custom_menu_items, budgets, and transactions.
    """
    payload_for_hash = {
        "version": data_dict.get("version", 2),
        "revision": data_dict.get("revision", 1),
        "settings": data_dict.get("settings", {}),
        "custom_menu_items": data_dict.get("custom_menu_items", []),
        "budgets": data_dict.get("budgets", []),
        "transactions": data_dict.get("transactions", []),
    }
    canonical_str = json.dumps(
        payload_for_hash,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':')
    )
    return hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

def verify_backup_payload(data_dict: dict) -> tuple[bool, str]:
    """
    Validates backup payload schema, Format v2 checksum, and every field using utils/validation.
    Returns (True, "OK") or (False, error_message).
    """
    if not isinstance(data_dict, dict):
        return False, "Payload must be a JSON object"

    version = data_dict.get("version", 1)
    transactions = data_dict.get("transactions", [])
    if not isinstance(transactions, list):
        return False, "'transactions' must be a list"

    # For Format v2 backups: verify mandatory checksum
    if version >= 2:
        checksum = data_dict.get("checksum")
        if not checksum or not isinstance(checksum, str):
            return False, "Missing or empty checksum in Format v2 backup"
        expected_checksum = compute_canonical_checksum(data_dict)
        if checksum != expected_checksum:
            return False, f"Checksum mismatch: expected {expected_checksum} but got {checksum}"

    # Validate every transaction entity with utils/validation
    for idx, tx in enumerate(transactions, 1):
        if not isinstance(tx, dict):
            return False, f"Transaction #{idx} is not a valid object"

        # Amount validation (Decimal, non-negative, non-zero, within 10 crore)
        try:
            amt = parse_decimal_amount(tx.get('amount'), allow_zero=False)
        except Exception as e:
            return False, f"Transaction #{idx} invalid amount: {e}"

        # Transaction type validation ('SENT' or 'RECEIVED')
        try:
            tt = validate_transaction_type(tx.get('transaction_type'))
        except Exception as e:
            return False, f"Transaction #{idx} invalid transaction_type: {e}"

        # UID validation (mandatory in v2, 32 hex chars)
        raw_uid = tx.get('uid')
        if version >= 2:
            if not raw_uid:
                return False, f"Transaction #{idx} missing mandatory UID in v2 backup"
            try:
                validate_uid(raw_uid)
            except Exception as e:
                return False, f"Transaction #{idx} invalid UID: {e}"
        elif raw_uid:
            try:
                validate_uid(raw_uid)
            except Exception as e:
                return False, f"Transaction #{idx} invalid UID: {e}"

        # String bounds validation
        try:
            validate_string_length(tx.get('person_name', ''), max_length=120, field_name=f"Tx #{idx} person_name")
            validate_string_length(tx.get('sender_name', ''), max_length=120, field_name=f"Tx #{idx} sender_name")
            validate_string_length(tx.get('recipient_name', ''), max_length=120, field_name=f"Tx #{idx} recipient_name")
            validate_string_length(tx.get('reference_number', ''), max_length=100, field_name=f"Tx #{idx} reference_number")
            validate_string_length(tx.get('category', 'General') or 'General', max_length=100, field_name=f"Tx #{idx} category")
        except Exception as e:
            return False, str(e)

    # Validate custom menu items if present
    custom_menu_items = data_dict.get("custom_menu_items", [])
    if not isinstance(custom_menu_items, list):
        return False, "'custom_menu_items' must be a list"
    for idx, dish in enumerate(custom_menu_items, 1):
        if not isinstance(dish, dict):
            return False, f"Menu item #{idx} is not a valid object"
        try:
            validate_string_length(dish.get('name', ''), max_length=100, field_name=f"Menu #{idx} name", required=True)
            parse_decimal_amount(dish.get('price'), allow_zero=False)
        except Exception as e:
            return False, f"Menu item #{idx} validation error: {e}"

    return True, "OK"

def preview_database_import(input_path: Path = None, data_dict: dict = None) -> dict:
    """
    Dry-run preview of importing a backup: validates schema/checksum and calculates
    counts for rows to be added, updated, and skipped without modifying the database.
    """
    path = input_path or BACKUP_JSON_PATH
    if data_dict is None:
        if not path.exists():
            return {'success': False, 'error': f"Backup file not found at {path}"}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data_dict = json.load(f)
        except Exception as e:
            return {'success': False, 'error': f"Invalid JSON in backup file: {e}"}

    valid, err_msg = verify_backup_payload(data_dict)
    if not valid:
        return {'success': False, 'error': err_msg}

    version = data_dict.get("version", 1)
    transactions = data_dict.get("transactions", [])

    to_add = 0
    to_update = 0
    to_skip = 0

    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, uid, updated_at, deleted_at, reference_number, transaction_type, amount, transaction_date, transaction_time, person_name FROM transactions")
            existing_rows = cursor.fetchall()

            if version >= 2:
                existing_by_uid = {r['uid']: dict(r) for r in existing_rows if r['uid']}
                for tx in transactions:
                    tx_uid = tx.get('uid')
                    if not tx_uid or tx_uid not in existing_by_uid:
                        to_add += 1
                    else:
                        local_row = existing_by_uid[tx_uid]
                        dt_inc = parse_utc_iso(tx.get('updated_at'))
                        dt_loc = parse_utc_iso(local_row.get('updated_at'))
                        inc_del = bool(tx.get('deleted_at'))
                        loc_del = bool(local_row.get('deleted_at'))

                        if dt_inc is not None and dt_loc is not None:
                            if dt_inc > dt_loc:
                                to_update += 1
                            elif dt_inc < dt_loc:
                                to_skip += 1
                            else:  # equal timestamps: tombstone wins
                                if inc_del and not loc_del:
                                    to_update += 1
                                else:
                                    to_skip += 1
                        elif dt_inc is not None and dt_loc is None:
                            to_update += 1
                        else:
                            to_skip += 1
            else:
                # v1: match live rows by non-empty reference_number or fingerprint
                live_refs = {r['reference_number'].strip() for r in existing_rows if r['deleted_at'] is None and r['reference_number']}
                live_fps = {
                    (
                        r['transaction_type'],
                        str(Decimal(str(r['amount'])).quantize(CENT)),
                        str(r['transaction_date']),
                        str(r['transaction_time'] or '').strip(),
                        str(r['person_name'] or '').strip()
                    )
                    for r in existing_rows if r['deleted_at'] is None
                }
                for tx in transactions:
                    ref = str(tx.get('reference_number') or '').strip()
                    try:
                        amt_dec = str(parse_decimal_amount(tx.get('amount', 0.0), allow_zero=False))
                    except Exception:
                        amt_dec = '0.00'
                    fp = (
                        tx.get('transaction_type'),
                        amt_dec,
                        str(tx.get('transaction_date')),
                        str(tx.get('transaction_time') or '').strip(),
                        str(tx.get('person_name') or '').strip()
                    )
                    if (ref and ref in live_refs) or (fp in live_fps):
                        to_skip += 1
                    else:
                        to_add += 1
                        if ref:
                            live_refs.add(ref)
                        live_fps.add(fp)

    return {
        'success': True,
        'to_add': to_add,
        'to_update': to_update,
        'to_skip': to_skip,
        'total': len(transactions),
        'version': version,
        'revision': data_dict.get('revision', 1),
        'exported_at': data_dict.get('exported_at', ''),
        'backup_balance': data_dict.get('balance', 0.0),
    }

def import_database_from_json(input_path: Path = None, data_dict: dict = None) -> dict:
    """
    Imports transactions, custom menu items, and settings from JSON into SQLite database.
    Order:
      1. Parse JSON
      2. Validate schema
      3. Verify checksum (missing or mismatched = reject)
      4. Validate every field with utils/validation
      5. Import inside ONE connection and ONE database transaction under LEDGER_LOCK.
         Any failure rolls back everything.
      6. Per-row rules for v2: match by uid; compare parsed UTC datetimes. Newer wins,
         older ignored, equal -> tombstone wins. Newer local tombstone always beats older live backup row.
         Idempotent on re-import.
      7. v1 backups (no uid): never match by local id. Skipped if live row has same reference
         or (type, amount, date, time, person). Otherwise insert with new generated uid.
      8. Recalculate balances inside the same connection.
      9. Compare recalculated balance with backup balance. On mismatch, report loudly.
    """
    path = input_path or BACKUP_JSON_PATH
    if data_dict is None:
        if not path.exists():
            logger.info(f"No backup JSON found at {path}")
            return {'success': False, 'error': f"Backup file not found at {path}"}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data_dict = json.load(f)
        except Exception as e:
            logger.error(f"Invalid JSON in backup file: {e}")
            return {'success': False, 'error': f"Invalid JSON in backup file: {e}"}

    # Steps 2, 3, 4: Validate schema, checksum, and all fields
    valid, err_msg = verify_backup_payload(data_dict)
    if not valid:
        logger.error(f"Backup verification rejected: {err_msg}")
        return {'success': False, 'error': err_msg}

    transactions = data_dict.get("transactions", [])
    settings = data_dict.get("settings", {})
    custom_menu_items = data_dict.get("custom_menu_items", [])
    version = data_dict.get("version", 1)

    with LEDGER_LOCK:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT * FROM transactions")
                existing_rows = cursor.fetchall()
                existing_by_uid = {r['uid']: dict(r) for r in existing_rows if r['uid']}

                inserted_count = 0
                updated_count = 0
                skipped_count = 0

                if version >= 2:
                    now_utc = utc_now_iso()
                    if data_dict.get('empty_ledger') and len(transactions) == 0:
                        cursor.execute("UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE deleted_at IS NULL", (now_utc, now_utc))
                    for tx in transactions:
                        tx_uid = tx['uid']
                        amt_dec = parse_decimal_amount(tx.get('amount'), allow_zero=False)
                        amt_val = float(amt_dec)
                        tt_val = validate_transaction_type(tx.get('transaction_type'))
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

                        occurred_at = tx.get('occurred_at') or build_occurred_at(tx.get('transaction_date'), tx.get('transaction_time'))
                        created_at = tx.get('created_at') or utc_now_iso()
                        incoming_updated = tx.get('updated_at') or utc_now_iso()

                        if tx_uid not in existing_by_uid:
                            cursor.execute('''
                                INSERT INTO transactions (
                                    transaction_type, amount, person_name, sender_name, recipient_name,
                                    upi_id, phone_number, transaction_date, transaction_time, reference_number,
                                    transaction_id, payment_app, bank_name, bank_account, payment_status,
                                    category, balance_before, balance_after, ocr_text, original_image_path,
                                    telegram_message_id, telegram_chat_id, uid, occurred_at, deleted_at, created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                tt_val, amt_val, p_name, s_name, r_name,
                                tx.get('upi_id', ''), tx.get('phone_number', ''), tx.get('transaction_date'),
                                tx.get('transaction_time', ''), ref_no, tx.get('transaction_id', ''),
                                tx.get('payment_app', ''), tx.get('bank_name', ''), tx.get('bank_account', ''),
                                tx.get('payment_status', 'SUCCESS'), cat_val, bal_before, bal_after,
                                tx.get('ocr_text', ''), tx.get('original_image_path', ''),
                                str(tx.get('telegram_message_id', '')), str(tx.get('telegram_chat_id', '')),
                                tx_uid, occurred_at, tx.get('deleted_at'), created_at, incoming_updated
                            ))
                            inserted_count += 1
                            existing_by_uid[tx_uid] = {
                                'id': cursor.lastrowid,
                                'uid': tx_uid,
                                'updated_at': incoming_updated,
                                'deleted_at': tx.get('deleted_at')
                            }
                        else:
                            local_row = existing_by_uid[tx_uid]
                            dt_inc = parse_utc_iso(incoming_updated)
                            dt_loc = parse_utc_iso(local_row.get('updated_at'))
                            inc_del = bool(tx.get('deleted_at'))
                            loc_del = bool(local_row.get('deleted_at'))

                            apply_incoming = False
                            if dt_inc is not None and dt_loc is not None:
                                if dt_inc > dt_loc:
                                    apply_incoming = True
                                elif dt_inc < dt_loc:
                                    apply_incoming = False
                                else:  # equal timestamps: tombstone wins
                                    if inc_del and not loc_del:
                                        apply_incoming = True
                                    else:
                                        apply_incoming = False
                            elif dt_inc is not None and dt_loc is None:
                                apply_incoming = True
                            else:
                                apply_incoming = False

                            if apply_incoming:
                                db_id = local_row['id']
                                cursor.execute('''
                                    UPDATE transactions SET
                                        transaction_type = ?, amount = ?, person_name = ?, sender_name = ?,
                                        recipient_name = ?, upi_id = ?, phone_number = ?, transaction_date = ?,
                                        transaction_time = ?, reference_number = ?, transaction_id = ?,
                                        payment_app = ?, bank_name = ?, bank_account = ?, payment_status = ?,
                                        category = ?, balance_before = ?, balance_after = ?, ocr_text = ?,
                                        original_image_path = ?, telegram_message_id = ?, telegram_chat_id = ?,
                                        occurred_at = ?, deleted_at = ?, updated_at = ?
                                    WHERE id = ?
                                ''', (
                                    tt_val, amt_val, p_name, s_name, r_name,
                                    tx.get('upi_id', ''), tx.get('phone_number', ''), tx.get('transaction_date'),
                                    tx.get('transaction_time', ''), ref_no, tx.get('transaction_id', ''),
                                    tx.get('payment_app', ''), tx.get('bank_name', ''), tx.get('bank_account', ''),
                                    tx.get('payment_status', 'SUCCESS'), cat_val, bal_before, bal_after,
                                    tx.get('ocr_text', ''), tx.get('original_image_path', ''),
                                    str(tx.get('telegram_message_id', '')), str(tx.get('telegram_chat_id', '')),
                                    occurred_at, tx.get('deleted_at'), incoming_updated, db_id
                                ))
                                updated_count += 1
                                local_row['updated_at'] = incoming_updated
                                local_row['deleted_at'] = tx.get('deleted_at')
                            else:
                                skipped_count += 1
                else:
                    # v1 backup (no uid): never match by local id.
                    live_refs = {r['reference_number'].strip() for r in existing_rows if r['deleted_at'] is None and r['reference_number']}
                    live_fps = {
                        (
                            r['transaction_type'],
                            str(Decimal(str(r['amount'])).quantize(CENT)),
                            str(r['transaction_date']),
                            str(r['transaction_time'] or '').strip(),
                            str(r['person_name'] or '').strip()
                        )
                        for r in existing_rows if r['deleted_at'] is None
                    }

                    for tx in transactions:
                        amt_dec = parse_decimal_amount(tx.get('amount'), allow_zero=False)
                        amt_val = float(amt_dec)
                        tt_val = validate_transaction_type(tx.get('transaction_type'))
                        ref_no = validate_string_length(tx.get('reference_number', ''), max_length=100)
                        p_name = validate_string_length(tx.get('person_name', ''), max_length=120)
                        s_name = validate_string_length(tx.get('sender_name', ''), max_length=120)
                        r_name = validate_string_length(tx.get('recipient_name', ''), max_length=120)
                        cat_val = validate_string_length(tx.get('category', 'General'), max_length=100) or 'General'

                        fp = (
                            tt_val,
                            str(amt_dec),
                            str(tx.get('transaction_date')),
                            str(tx.get('transaction_time') or '').strip(),
                            p_name
                        )
                        if (ref_no and ref_no in live_refs) or (fp in live_fps):
                            skipped_count += 1
                        else:
                            new_uid = uuid.uuid4().hex
                            now_utc = utc_now_iso()
                            occurred_at = build_occurred_at(tx.get('transaction_date'), tx.get('transaction_time'))
                            cursor.execute('''
                                INSERT INTO transactions (
                                    transaction_type, amount, person_name, sender_name, recipient_name,
                                    upi_id, phone_number, transaction_date, transaction_time, reference_number,
                                    transaction_id, payment_app, bank_name, bank_account, payment_status,
                                    category, balance_before, balance_after, ocr_text, original_image_path,
                                    telegram_message_id, telegram_chat_id, uid, occurred_at, deleted_at, created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                tt_val, amt_val, p_name, s_name, r_name,
                                tx.get('upi_id', ''), tx.get('phone_number', ''), tx.get('transaction_date'),
                                tx.get('transaction_time', ''), ref_no, tx.get('transaction_id', ''),
                                tx.get('payment_app', ''), tx.get('bank_name', ''), tx.get('bank_account', ''),
                                tx.get('payment_status', 'SUCCESS'), cat_val, 0.0, 0.0,
                                tx.get('ocr_text', ''), tx.get('original_image_path', ''),
                                str(tx.get('telegram_message_id', '')), str(tx.get('telegram_chat_id', '')),
                                new_uid, occurred_at, None, now_utc, now_utc
                            ))
                            inserted_count += 1
                            if ref_no:
                                live_refs.add(ref_no)
                            live_fps.add(fp)

                    logger.info(f"v1 backup import: {inserted_count} added, {skipped_count} skipped.")

                # Restore custom cafeteria menu dishes
                for dish in custom_menu_items:
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

                # Restore initial_balance and monthly_budget from settings if present
                for k, v in settings.items():
                    if k in ('initial_balance', 'monthly_budget'):
                        try:
                            v = str(float(parse_decimal_amount(v, allow_zero=True)))
                            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
                        except Exception:
                            pass

                # Step 8: Recalculate balance chain over live rows inside the same connection
                from services.balance_service import recalculate_in_connection
                derived_bal = recalculate_in_connection(conn)

                # Step 9: Compare recalculated balance with backup's balance
                stored_bal = data_dict.get('balance')
                balance_match = True
                balance_discrepancy = 0.0
                stored_dec = None
                derived_dec = Decimal(str(derived_bal)).quantize(CENT)

                if stored_bal is not None:
                    try:
                        stored_dec = Decimal(str(stored_bal)).quantize(CENT)
                        if stored_dec != derived_dec:
                            balance_match = False
                            balance_discrepancy = float(derived_dec - stored_dec)
                            logger.error(
                                f"⚠️ BALANCE MISMATCH AFTER RESTORE: Recalculated balance is ₹{derived_dec} "
                                f"but backup stated ₹{stored_dec} (discrepancy: ₹{balance_discrepancy})!"
                            )
                    except Exception as b_err:
                        logger.warning(f"Could not parse backup balance for comparison: {b_err}")

                now_utc = utc_now_iso()
                cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)", (now_utc,))
                cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '0', ?)", (now_utc,))

                # Advance revision
                cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
                rev_row = cursor.fetchone()
                local_rev = int(rev_row['value']) if rev_row and rev_row['value'] and str(rev_row['value']).isdigit() else 1
                backup_rev = int(data_dict.get('revision', 1))
                final_rev = max(local_rev, backup_rev)
                cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_revision', ?, ?)", (str(final_rev), now_utc))

                conn.commit()

            logger.info(f"Imported database from JSON: {inserted_count} inserted, {updated_count} updated, {skipped_count} skipped.")
            return {
                'success': True,
                'inserted': inserted_count,
                'updated': updated_count,
                'skipped': skipped_count,
                'total': len(transactions),
                'balance_match': balance_match,
                'derived_balance': float(derived_dec),
                'backup_balance': float(stored_dec) if stored_dec is not None else None,
                'balance_discrepancy': balance_discrepancy,
            }
        except Exception as e:
            logger.error(f"Error importing database from JSON (rolled back): {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

def record_confirmed_backup(timestamp_iso: str = None, revision: int = None) -> str:
    """Records the timestamp of a confirmed backup upload in the settings table and clears dirty state."""
    from utils.dates import utc_now_iso
    ts = timestamp_iso or utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_confirmed_backup_at', ?, ?)",
                (ts, ts)
            )
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '0', ?)",
                (ts,)
            )
            if revision is not None:
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_backup_ok_revision', ?, ?)",
                    (str(revision), ts)
                )
            else:
                cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
                r_row = cursor.fetchone()
                if r_row and r_row['value']:
                    cursor.execute(
                        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_backup_ok_revision', ?, ?)",
                        (str(r_row['value']), ts)
                    )
            conn.commit()
    logger.info(f"Recorded confirmed backup upload timestamp: {ts}")
    return ts

def mark_dirty():
    """Marks database dirty in settings table."""
    try:
        now_utc = utc_now_iso()
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '1', ?)",
                    (now_utc,)
                )
                conn.commit()
    except Exception as e:
        logger.debug(f"Notice marking dirty: {e}")

async def backup_to_telegram(bot, chat_id: str = None, timeout: float = 20.0, force: bool = False) -> bool:
    """
    Exports current database to JSON (Format v2) and uploads it to Telegram as a pinned backup document.
    Never uploads if database contains 0 transactions (including tombstones).
    Refuses upload if local revision is lower than newest cloud revision.
    Rotates backup messages retaining the last 7; older ones are removed/unpinned only after upload confirmation.
    Awaits cloud backup with configurable timeout (default 20s).
    """
    async def _do_backup() -> bool:
        target_chat = chat_id or TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
        if not target_chat or not bot:
            logger.warning("No Telegram chat or bot available for cloud backup.")
            mark_dirty()
            return False

        # Blocked rule: if backup_blocked is true, never upload
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'backup_blocked'")
            b_row = cursor.fetchone()
            if b_row and str(b_row['value']).lower() in ('1', 'true'):
                logger.warning("Backup blocked: database is in backup_blocked state pending restore or first transaction.")
                return False
            
        with EXPORT_LOCK:
            data = export_database_to_json()

        if not data or not BACKUP_JSON_PATH.exists():
            logger.warning("No valid backup data generated or backup file missing.")
            mark_dirty()
            return False
            
        tx_count = data.get("transaction_count", 0)
        live_count = data.get("live_count", 0)
        rev = data.get("revision", 1)

        # Check if backup is blocked or database is uninitialized
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'backup_blocked'")
            b_row = cursor.fetchone()
            if b_row and b_row['value'] in ('1', 'true', 'True') and not force:
                logger.warning("Refusing cloud backup: database backup is blocked (backup_blocked=1).")
                return False
            cursor.execute("SELECT value FROM settings WHERE key = 'database_initialized'")
            i_row = cursor.fetchone()
            is_init = bool(i_row and i_row['value'] in ('1', 'true', 'True'))

        if not is_init and tx_count == 0 and not force:
            logger.warning("Empty rule enforced: database is uninitialized with 0 transactions. Refusing cloud backup.")
            return False

        # Cloud revision check: refuse if local revision is lower than newest cloud revision
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'last_backup_ok_revision'")
            row = cursor.fetchone()
            last_ok_rev = int(row['value']) if row and row['value'] and str(row['value']).isdigit() else 0

        if rev < last_ok_rev and not force:
            logger.warning(f"Refusing to upload backup: local revision ({rev}) is lower than last confirmed cloud revision ({last_ok_rev}).")
            return False

        try:
            chat = await bot.get_chat(chat_id=target_chat)
            pinned = getattr(chat, 'pinned_message', None)
            if pinned and pinned.caption:
                m = re.search(r"Rev\s+(\d+)", pinned.caption)
                if m:
                    cloud_rev = int(m.group(1))
                    if rev < cloud_rev and not force:
                        logger.warning(f"Refusing to upload backup: local revision ({rev}) is lower than cloud revision ({cloud_rev}).")
                        return False
        except Exception as chat_err:
            logger.debug(f"Notice inspecting cloud chat pinned revision: {chat_err}")

        ledger_status = "live" if live_count > 0 else "empty active ledger"
        caption = f"#PAYMENT_TRACKER_BACKUP_V2 ☁️ Auto-Backup Rev {rev} ({tx_count} records, {live_count} {ledger_status})"

        with open(BACKUP_JSON_PATH, 'rb') as doc_file:
            msg = await bot.send_document(
                chat_id=target_chat,
                document=doc_file,
                filename="payment_tracker_backup.json",
                caption=caption,
                disable_notification=True
            )
            
        # Pin new message
        try:
            await bot.pin_chat_message(
                chat_id=target_chat,
                message_id=msg.message_id,
                disable_notification=True
            )
        except Exception as pin_err:
            logger.debug(f"Notice pinning new backup message: {pin_err}")

        # Rotate backup messages: retain last 7 message IDs
        to_prune = []
        now_utc = utc_now_iso()
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM settings WHERE key = 'telegram_backup_message_ids'")
                row = cursor.fetchone()
                try:
                    msg_ids = json.loads(row['value']) if row and row['value'] else []
                except Exception:
                    msg_ids = []
                if not isinstance(msg_ids, list):
                    msg_ids = []

                msg_ids.append(msg.message_id)
                if len(msg_ids) > 7:
                    to_prune = msg_ids[:-7]
                    msg_ids = msg_ids[-7:]

                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('telegram_backup_message_ids', ?, ?)",
                    (json.dumps(msg_ids), now_utc)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_telegram_backup_at', ?, ?)",
                    (now_utc, now_utc)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_confirmed_backup_at', ?, ?)",
                    (now_utc, now_utc)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_backup_ok_revision', ?, ?)",
                    (str(rev), now_utc)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '0', ?)",
                    (now_utc,)
                )
                conn.commit()

        # Remove or unpin older backup messages only after confirmed upload
        for old_id in to_prune:
            try:
                await bot.delete_message(chat_id=target_chat, message_id=old_id)
            except Exception:
                try:
                    await bot.unpin_chat_message(chat_id=target_chat, message_id=old_id)
                except Exception:
                    pass

        logger.info(f"Successfully backed up database (v2, rev {rev}) to Telegram cloud.")
        return True

    try:
        return await asyncio.wait_for(_do_backup(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Telegram backup timed out after {timeout} seconds.")
        mark_dirty()
        return False
    except Exception as e:
        logger.error(f"Failed to backup database to Telegram: {e}", exc_info=True)
        mark_dirty()
        return False

def restore_local_fallback_if_valid() -> bool:
    """Restores from local BACKUP_JSON_PATH only if its checksum and schema are valid."""
    if not BACKUP_JSON_PATH.exists():
        return False
    try:
        with open(BACKUP_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        valid, err = verify_backup_payload(data)
        if not valid:
            logger.warning(f"Local backup JSON fallback rejected: checksum or schema invalid ({err})")
            return False
        res = import_database_from_json(data_dict=data)
        return bool(res.get('success'))
    except Exception as e:
        logger.warning(f"Error reading local backup fallback: {e}")
        return False

async def restore_from_telegram(bot, chat_id: str = None) -> bool:
    """
    Retrieves the latest pinned backup document from Telegram and restores the database.
    Falls back to local JSON backup ONLY if its checksum is valid.
    """
    target_chat = chat_id or TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
    if not target_chat or not bot:
        logger.warning("No Telegram chat or bot available for cloud restore.")
        return restore_local_fallback_if_valid()
        
    try:
        chat = await bot.get_chat(chat_id=target_chat)
        pinned = chat.pinned_message
        if not pinned or not pinned.document:
            logger.info("No pinned backup document found in Telegram chat.")
            return restore_local_fallback_if_valid()
            
        is_backup = (
            (pinned.caption and "#PAYMENT_TRACKER_BACKUP" in pinned.caption) or
            ("backup" in (pinned.document.file_name or "").lower())
        )
        if not is_backup:
            logger.info("Pinned message is not a payment tracker backup.")
            return restore_local_fallback_if_valid()
            
        logger.info(f"Found pinned cloud backup: {pinned.document.file_name}. Downloading...")
        file = await bot.get_file(pinned.document.file_id)
        download_path = DATA_DIR / "temp_cloud_backup.json"
        try:
            await file.download_to_drive(custom_path=download_path)
            res = import_database_from_json(input_path=download_path)
            return bool(res.get('success'))
        finally:
            if download_path.exists():
                try:
                    download_path.unlink()
                except OSError as cleanup_err:
                    logger.warning(f"Could not remove temp cloud backup file {download_path}: {cleanup_err}")
    except Exception as e:
        logger.error(f"Error restoring backup from Telegram: {e}", exc_info=True)
        return restore_local_fallback_if_valid()
