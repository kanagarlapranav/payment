import uuid
from datetime import datetime
from database.models import Transaction
from database.db import get_db_connection, LEDGER_LOCK
from utils.dates import build_occurred_at, utc_now_iso
from utils.validation import (
    parse_decimal_amount,
    validate_transaction_type,
    validate_uid,
    validate_string_length,
)
from config import logger

def increment_revision_and_mark_dirty(conn) -> int:
    """
    Increments the persisted backup_revision counter in settings by 1 and marks is_dirty = '1'.
    Must be called inside the active database transaction of a committed mutation under LEDGER_LOCK.
    Returns the new revision integer.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
    row = cursor.fetchone()
    current_rev = int(row['value']) if row and str(row['value']).isdigit() else 1
    new_rev = current_rev + 1
    now_utc = utc_now_iso()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_revision', ?, ?)",
        (str(new_rev), now_utc)
    )
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '1', ?)",
        (now_utc,)
    )
    return new_rev

def insert_transaction_with_balance(t: Transaction) -> int:
    """
    Inserts a transaction and recalculates the balance chain atomically.
    Executes in ONE connection and ONE database transaction under LEDGER_LOCK:
      1. Validates all inputs (amount, type, UID, string lengths).
      2. Checks duplicate reference_number among live transactions (deleted_at IS NULL).
      3. Computes occurred_at, created_at, updated_at using utc_now_iso().
      4. Inserts the transaction row with placeholder balances.
      5. Recalculates the entire ledger chain using recalculate_in_connection(conn).
      6. Commits the transaction (or rolls back on ANY failure, leaving balance unchanged).
    A back-dated receipt produces a correct chain.
    Does NOT export backup here.
    """
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection

        # Step 1: Validate inputs
        dec_amount = parse_decimal_amount(t.amount, allow_zero=False)
        t.amount = float(dec_amount)
        t.transaction_type = validate_transaction_type(t.transaction_type)
        category = validate_string_length(getattr(t, 'category', 'General') or 'General', max_length=100, field_name="Category")
        t.category = category

        raw_uid = getattr(t, 'uid', None)
        if raw_uid:
            tx_uid = validate_uid(raw_uid)
        else:
            tx_uid = uuid.uuid4().hex
        t.uid = tx_uid

        t.person_name = validate_string_length(t.person_name, max_length=120, field_name="Person name")
        t.sender_name = validate_string_length(t.sender_name, max_length=120, field_name="Sender name")
        t.recipient_name = validate_string_length(t.recipient_name, max_length=120, field_name="Recipient name")
        t.reference_number = validate_string_length(t.reference_number, max_length=100, field_name="Reference number")

        # Step 2: Compute occurred_at and timestamps
        occurred_at = build_occurred_at(t.transaction_date, t.transaction_time)
        t.occurred_at = occurred_at

        now_utc = utc_now_iso()
        created_at = getattr(t, 'created_at', None) or now_utc
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        updated_at = now_utc

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Step 3: Check duplicate reference among live rows
            if t.reference_number:
                cursor.execute(
                    "SELECT id FROM transactions WHERE reference_number = ? AND deleted_at IS NULL",
                    (t.reference_number,)
                )
                dup = cursor.fetchone()
                if dup:
                    raise ValueError(f"Duplicate live reference number: {t.reference_number}")

            # Step 4: Insert row
            query = '''
                INSERT INTO transactions (
                    transaction_type, amount, person_name, sender_name, recipient_name,
                    upi_id, phone_number, transaction_date, transaction_time, reference_number,
                    transaction_id, payment_app, bank_name, bank_account, payment_status,
                    category, balance_before, balance_after, ocr_text, original_image_path,
                    telegram_message_id, telegram_chat_id, uid, deleted_at,
                    occurred_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            values = (
                t.transaction_type, t.amount, t.person_name, t.sender_name, t.recipient_name,
                t.upi_id, t.phone_number, t.transaction_date, t.transaction_time, t.reference_number,
                t.transaction_id, t.payment_app, t.bank_name, t.bank_account, t.payment_status,
                category, 0.0, 0.0, t.ocr_text, t.original_image_path,
                t.telegram_message_id, t.telegram_chat_id, tx_uid, getattr(t, 'deleted_at', None),
                occurred_at, created_at, updated_at
            )
            cursor.execute(query, values)
            new_id = cursor.lastrowid
            t.id = new_id

            # Step 5: Recalculate chain in this connection
            recalculate_in_connection(conn)
            increment_revision_and_mark_dirty(conn)
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)", (now_utc,))
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '0', ?)", (now_utc,))

            # Step 6: Commit happens on exit of context manager
            return new_id

def insert_transaction(t: Transaction) -> int:
    """Inserts a new transaction into the database with decimal validation, occurred_at, and thread locking."""
    with LEDGER_LOCK:
        dec_amount = parse_decimal_amount(t.amount, allow_zero=False)
        t.amount = float(dec_amount)
        t.transaction_type = validate_transaction_type(t.transaction_type)
        
        category = validate_string_length(getattr(t, 'category', 'General') or 'General', max_length=100, field_name="Category")
        t.category = category
        
        raw_uid = getattr(t, 'uid', None)
        if raw_uid:
            tx_uid = validate_uid(raw_uid)
        else:
            tx_uid = uuid.uuid4().hex
        t.uid = tx_uid
        
        t.person_name = validate_string_length(t.person_name, max_length=120, field_name="Person name")
        t.sender_name = validate_string_length(t.sender_name, max_length=120, field_name="Sender name")
        t.recipient_name = validate_string_length(t.recipient_name, max_length=120, field_name="Recipient name")
        t.reference_number = validate_string_length(t.reference_number, max_length=100, field_name="Reference number")
        
        t.balance_before = round(float(t.balance_before), 2)
        t.balance_after = round(float(t.balance_after), 2)

        occurred_at = build_occurred_at(t.transaction_date, t.transaction_time)
        t.occurred_at = occurred_at
        now_utc = utc_now_iso()
        created_at = getattr(t, 'created_at', None) or now_utc
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        updated_at = now_utc

        query = '''
            INSERT INTO transactions (
                transaction_type, amount, person_name, sender_name, recipient_name,
                upi_id, phone_number, transaction_date, transaction_time, reference_number,
                transaction_id, payment_app, bank_name, bank_account, payment_status,
                category, balance_before, balance_after, ocr_text, original_image_path,
                telegram_message_id, telegram_chat_id, uid, deleted_at,
                occurred_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        
        values = (
            t.transaction_type, t.amount, t.person_name, t.sender_name, t.recipient_name,
            t.upi_id, t.phone_number, t.transaction_date, t.transaction_time, t.reference_number,
            t.transaction_id, t.payment_app, t.bank_name, t.bank_account, t.payment_status,
            category, t.balance_before, t.balance_after, t.ocr_text, t.original_image_path,
            t.telegram_message_id, t.telegram_chat_id, tx_uid, getattr(t, 'deleted_at', None),
            occurred_at, created_at, updated_at
        )
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, values)
            new_id = cursor.lastrowid
            t.id = new_id
            increment_revision_and_mark_dirty(conn)
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)", (now_utc,))
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '0', ?)", (now_utc,))
            conn.commit()
            return new_id

def get_transaction_by_reference(reference_number: str):
    """Fetches a transaction by its reference number."""
    if not reference_number:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE reference_number = ? AND deleted_at IS NULL", (reference_number,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_recent_transactions(limit: int = 10):
    """Fetches recent transactions ordered by occurred_at, created_at, and id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE deleted_at IS NULL ORDER BY occurred_at DESC, created_at DESC, id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_transactions_by_date(target_date):
    """Fetches transactions for a specific date."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE transaction_date = ? AND deleted_at IS NULL ORDER BY occurred_at ASC, created_at ASC, id ASC", (target_date,))
        return [dict(row) for row in cursor.fetchall()]

def get_all_transactions():
    """Fetches all transactions for export (newest first)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE deleted_at IS NULL ORDER BY occurred_at DESC, created_at DESC, id DESC")
        return [dict(row) for row in cursor.fetchall()]

def get_all_transactions_asc():
    """Fetches all transactions in ascending order (oldest first, ID #1 first)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE deleted_at IS NULL ORDER BY occurred_at ASC, created_at ASC, id ASC")
        return [dict(row) for row in cursor.fetchall()]

def search_transactions(
    query_text: str = "",
    target_date = None,
    month: int = None,
    year: int = None,
    tx_type: str = "",
    person: str = "",
    exact_amount: float = None,
    sort_by: str = "date_desc",
    limit: int = 50
):
    """Flexible query function for searching, filtering, and sorting transactions."""
    conditions = ["deleted_at IS NULL"]
    params = []
    
    if exact_amount is not None and float(exact_amount) > 0:
        conditions.append("amount = ?")
        params.append(float(exact_amount))

    if target_date:
        conditions.append("transaction_date = ?")
        params.append(str(target_date))
    elif month and year:
        # SQLite strftime for month and year
        month_str = f"{year:04d}-{month:02d}"
        conditions.append("strftime('%Y-%m', transaction_date) = ?")
        params.append(month_str)
    elif year:
        conditions.append("strftime('%Y', transaction_date) = ?")
        params.append(str(year))
        
    if tx_type:
        conditions.append("transaction_type = ?")
        params.append(tx_type.upper())
        
    if person:
        conditions.append("(person_name LIKE ? OR sender_name LIKE ? OR recipient_name LIKE ?)")
        p_pattern = f"%{person}%"
        params.extend([p_pattern, p_pattern, p_pattern])
        
    if query_text:
        conditions.append("(person_name LIKE ? OR sender_name LIKE ? OR recipient_name LIKE ? OR reference_number LIKE ? OR bank_name LIKE ? OR ocr_text LIKE ?)")
        q_pattern = f"%{query_text}%"
        params.extend([q_pattern, q_pattern, q_pattern, q_pattern, q_pattern, q_pattern])

        
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    # Sorting
    sort_map = {
        "date_desc": "occurred_at DESC, created_at DESC, id DESC",
        "date_asc": "occurred_at ASC, created_at ASC, id ASC",
        "amount_desc": "amount DESC, occurred_at DESC",
        "amount_asc": "amount ASC, occurred_at ASC",
        "created_desc": "created_at DESC, id DESC"
    }
    order_clause = sort_map.get(sort_by, "occurred_at DESC, created_at DESC")
    
    sql = f"SELECT * FROM transactions {where_clause} ORDER BY {order_clause} LIMIT ?"
    params.append(limit)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

def get_monthly_summary(year: int, month: int):
    """Calculates summary statistics for a given month."""
    month_str = f"{year:04d}-{month:02d}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions 
            WHERE strftime('%Y-%m', transaction_date) = ? AND deleted_at IS NULL
            GROUP BY transaction_type
        """, (month_str,))
        rows = cursor.fetchall()
        
        total_sent = 0.0
        total_received = 0.0
        tx_count = 0
        
        for r in rows:
            tx_count += r['count']
            if r['transaction_type'] == 'SENT':
                total_sent = r['total_amount'] or 0.0
            elif r['transaction_type'] == 'RECEIVED':
                total_received = r['total_amount'] or 0.0
                
        # Top recipient (most money sent to)
        cursor.execute("""
            SELECT person_name, SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT' AND person_name != '' AND deleted_at IS NULL
            GROUP BY person_name
            ORDER BY total DESC LIMIT 1
        """, (month_str,))
        top_sent_row = cursor.fetchone()
        top_recipient = dict(top_sent_row) if top_sent_row else None
        
        return {
            'year': year,
            'month': month,
            'total_sent': total_sent,
            'total_received': total_received,
            'net_savings': total_received - total_sent,
            'tx_count': tx_count,
            'top_recipient': top_recipient
        }

def get_transaction_by_id(tx_id: int):
    """Fetches a transaction by its ID (live only)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE id = ? AND deleted_at IS NULL", (tx_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_transaction_by_uid(uid: str, live_only: bool = False):
    """Fetches a transaction by its permanent UID (live or deleted, or live only if requested)."""
    if not uid:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if live_only:
            cursor.execute("SELECT * FROM transactions WHERE uid = ? AND deleted_at IS NULL", (uid,))
        else:
            cursor.execute("SELECT * FROM transactions WHERE uid = ?", (uid,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_live_transaction_by_uid(uid: str):
    """Fetches an active, non-deleted transaction by permanent UID."""
    return get_transaction_by_uid(uid, live_only=True)

ALLOWED_UPDATE_COLUMNS = {
    'amount', 'transaction_type', 'person_name', 'sender_name', 'recipient_name',
    'payment_app', 'transaction_date', 'transaction_time', 'bank_name',
    'reference_number', 'category', 'raw_text', 'payment_status', 'note',
    'uid', 'occurred_at', 'confidence', 'ocr_text'
}

def update_transaction(tx_id: int, updates: dict) -> bool:
    """Updates specific fields of an active transaction with validation, occurred_at recalculation, and thread locking."""
    if not updates:
        return False

    # Enforce whitelist of allowed columns
    for key in updates.keys():
        if key not in ALLOWED_UPDATE_COLUMNS:
            raise ValueError(f"Disallowed column in updates: '{key}'. Allowed columns are: {sorted(ALLOWED_UPDATE_COLUMNS)}")

    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection

        validated_updates = dict(updates)
        if 'amount' in validated_updates:
            validated_updates['amount'] = float(parse_decimal_amount(validated_updates['amount'], allow_zero=False))
        if 'transaction_type' in validated_updates:
            validated_updates['transaction_type'] = validate_transaction_type(validated_updates['transaction_type'])
        if 'uid' in validated_updates and validated_updates['uid']:
            validated_updates['uid'] = validate_uid(validated_updates['uid'])
        if 'person_name' in validated_updates:
            validated_updates['person_name'] = validate_string_length(validated_updates['person_name'], max_length=120, field_name="Person name")
        if 'sender_name' in validated_updates:
            validated_updates['sender_name'] = validate_string_length(validated_updates['sender_name'], max_length=120, field_name="Sender name")
        if 'recipient_name' in validated_updates:
            validated_updates['recipient_name'] = validate_string_length(validated_updates['recipient_name'], max_length=120, field_name="Recipient name")
        if 'reference_number' in validated_updates:
            validated_updates['reference_number'] = validate_string_length(validated_updates['reference_number'], max_length=100, field_name="Reference number")
        if 'category' in validated_updates:
            validated_updates['category'] = validate_string_length(validated_updates['category'], max_length=100, field_name="Category")
            
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Ensure the row is active/live (deleted_at IS NULL)
            cursor.execute("SELECT id, transaction_date, transaction_time FROM transactions WHERE id = ? AND deleted_at IS NULL", (tx_id,))
            cur_row = cursor.fetchone()
            if not cur_row:
                return False

            # If transaction_date or transaction_time changed, recalculate occurred_at
            if 'transaction_date' in validated_updates or 'transaction_time' in validated_updates:
                new_date = validated_updates.get('transaction_date', cur_row['transaction_date'])
                new_time = validated_updates.get('transaction_time', cur_row['transaction_time'])
                validated_updates['occurred_at'] = build_occurred_at(new_date, new_time)

            now_utc = utc_now_iso()
            validated_updates['updated_at'] = now_utc

            set_clause = ", ".join([f"{k} = ?" for k in validated_updates.keys()])
            values = list(validated_updates.values()) + [tx_id]
            cursor.execute(f"UPDATE transactions SET {set_clause} WHERE id = ? AND deleted_at IS NULL", values)
            success = cursor.rowcount > 0

            # If balance-impacting fields changed, recalculate the chain in this connection
            balance_fields = {'amount', 'transaction_type', 'transaction_date', 'transaction_time', 'occurred_at'}
            if any(f in validated_updates for f in balance_fields):
                recalculate_in_connection(conn)

            if success:
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return success

def delete_transaction(tx_id: int) -> bool:
    """
    Soft-deletes a transaction by setting deleted_at and updated_at using utc_now_iso().
    Preserves permanent uid and recalculates balance chain over remaining live rows
    all in one database transaction under LEDGER_LOCK.
    Does NOT purge tombstones inside the delete path.
    Does NOT resequence transaction IDs.
    """
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now_utc, now_utc, tx_id)
            )
            deleted = (cursor.rowcount == 1)

            if deleted:
                recalculate_in_connection(conn)
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return deleted

def delete_transaction_by_uid(uid: str) -> bool:
    """
    Soft-deletes a transaction by permanent uid under LEDGER_LOCK in one database transaction.
    Recalculates balance chain over live rows. Does not purge tombstones.
    """
    if not uid:
        return False
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection
        now_utc = utc_now_iso()
        valid_uid = validate_uid(uid)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE uid = ? AND deleted_at IS NULL",
                (now_utc, now_utc, valid_uid)
            )
            deleted = (cursor.rowcount == 1)

            if deleted:
                recalculate_in_connection(conn)
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return deleted

def restore_soft_deleted_transaction(uid: str = None, tx_id: int = None) -> bool:
    """
    Restores a soft-deleted transaction by setting deleted_at = NULL and updated_at = utc_now_iso()
    WHERE uid = ? AND deleted_at IS NOT NULL.
    Returns success only if exactly one row changed, then recalculates the chain under LEDGER_LOCK.
    """
    if not uid and not tx_id:
        return False
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            target_uid = uid
            if not target_uid and tx_id:
                cursor.execute("SELECT uid FROM transactions WHERE id = ?", (tx_id,))
                row = cursor.fetchone()
                if not row or not row['uid']:
                    return False
                target_uid = row['uid']

            valid_uid = validate_uid(target_uid)
            cursor.execute(
                "UPDATE transactions SET deleted_at = NULL, updated_at = ? WHERE uid = ? AND deleted_at IS NOT NULL",
                (now_utc, valid_uid)
            )
            restored = (cursor.rowcount == 1)

            if restored:
                recalculate_in_connection(conn)
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return restored

def update_balance_setting(new_balance: float):
    """Updates the current balance in the settings table under LEDGER_LOCK."""
    with LEDGER_LOCK:
        dec_bal = parse_decimal_amount(new_balance, allow_zero=True) if new_balance >= 0 else round(float(new_balance), 2)
        bal_str = str(float(dec_bal)) if hasattr(dec_bal, '__float__') else str(round(float(new_balance), 2))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('current_balance', ?, ?)", (bal_str, utc_now_iso()))
            increment_revision_and_mark_dirty(conn)
            conn.commit()

def get_balance_setting() -> float:
    """Gets the current balance from the settings table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        row = cursor.fetchone()
        return float(row['value']) if row else 0.0

def get_budget_setting() -> float:
    """Gets the monthly budget limit from the settings table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'monthly_budget'")
        row = cursor.fetchone()
        return float(row['value']) if row else 0.0

def set_budget_setting(amount: float):
    """Sets the monthly budget limit in the settings table under LEDGER_LOCK."""
    with LEDGER_LOCK:
        dec_amount = parse_decimal_amount(amount, allow_zero=True)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('monthly_budget', ?, ?)", (str(float(dec_amount)), utc_now_iso()))
            increment_revision_and_mark_dirty(conn)
            conn.commit()

def get_monthly_spending(year: int, month: int) -> float:
    """Gets the total SENT amount for a given month."""
    month_str = f"{year:04d}-{month:02d}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT' AND deleted_at IS NULL
        """, (month_str,))
        row = cursor.fetchone()
        return float(row['total']) if (row and row['total'] is not None) else 0.0

def get_category_summary(year: int, month: int):
    """Gets breakdown of spending (SENT) and income (RECEIVED) by category for a month."""
    month_str = f"{year:04d}-{month:02d}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                category,
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND deleted_at IS NULL
            GROUP BY category, transaction_type
            ORDER BY total_amount DESC
        """, (month_str,))
        return [dict(row) for row in cursor.fetchall()]

def get_daily_summary_stats(target_date_str: str):
    """Calculates summary statistics for a specific date (YYYY-MM-DD)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions 
            WHERE transaction_date = ? AND deleted_at IS NULL
            GROUP BY transaction_type
        """, (target_date_str,))
        rows = cursor.fetchall()
        
        total_sent = 0.0
        total_received = 0.0
        tx_count = 0
        
        for r in rows:
            tx_count += r['count']
            if r['transaction_type'] == 'SENT':
                total_sent = float(r['total_amount'] or 0.0)
            elif r['transaction_type'] == 'RECEIVED':
                total_received = float(r['total_amount'] or 0.0)
                
        # Get list of transactions for the day
        cursor.execute("""
            SELECT id, transaction_type, amount, person_name, category, payment_app, transaction_time, balance_after
            FROM transactions
            WHERE transaction_date = ? AND deleted_at IS NULL
            ORDER BY id ASC
        """, (target_date_str,))
        transactions = [dict(row) for row in cursor.fetchall()]
        
        return {
            'date': target_date_str,
            'total_sent': total_sent,
            'total_received': total_received,
            'net_change': total_received - total_sent,
            'tx_count': tx_count,
            'transactions': transactions
        }

def get_cafeteria_transactions(limit: int = 50):
    """Fetches transactions related to Vikraman Nair / Cafeteria."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM transactions 
            WHERE (lower(person_name) LIKE '%vikraman%' 
               OR lower(person_name) LIKE '%cafeteria%' 
               OR lower(person_name) LIKE '%canteen%'
               OR lower(upi_id) LIKE '%vikraman%')
               AND deleted_at IS NULL
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


# --- Payee Category Memory ---

def get_payee_category(payee_name: str) -> str | None:
    """Retrieves remembered category for a payee if available."""
    if not payee_name or not payee_name.strip():
        return None
    normalized = payee_name.strip().lower()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT category FROM payee_categories WHERE lower(payee_name) = ?", (normalized,))
        row = cursor.fetchone()
        if row:
            return row['category']
        # Also check existing transactions history as fallback
        cursor.execute("""
            SELECT category FROM transactions 
            WHERE lower(person_name) = ? AND category IS NOT NULL AND category != 'General' AND deleted_at IS NULL
            ORDER BY id DESC LIMIT 1
        """, (normalized,))
        t_row = cursor.fetchone()
        return t_row['category'] if t_row else None

def remember_payee_category(payee_name: str, category: str):
    """Upserts payee -> category preference in payee_categories."""
    if not payee_name or not category or not payee_name.strip():
        return
    normalized = payee_name.strip().lower()
    now_utc = utc_now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payee_categories (payee_name, category, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(payee_name) DO UPDATE SET
                category = excluded.category,
                updated_at = excluded.updated_at
        """, (normalized, category, now_utc))
        conn.commit()


# --- Duplicate Detection ---

def find_potential_duplicate(amount: float, reference_number: str = None, person_name: str = None, tx_date: str = None):
    """Checks if a similar transaction already exists (by reference number or amount/payee/date)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check by reference number first (strongest indicator)
        if reference_number and len(str(reference_number).strip()) > 3:
            clean_ref = str(reference_number).strip()
            cursor.execute("SELECT * FROM transactions WHERE reference_number = ? AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (clean_ref,))
            row = cursor.fetchone()
            if row:
                res = dict(row)
                res['match_reason'] = f"same ref …{clean_ref[-4:]}"
                return res
        
        # Check by amount and person within same date or near date
        if amount and amount > 0:
            if person_name and person_name.strip():
                clean_person = person_name.strip().lower()
                if tx_date:
                    cursor.execute("""
                        SELECT * FROM transactions 
                        WHERE abs(amount - ?) < 0.01 
                          AND lower(person_name) LIKE ?
                          AND abs(julianday(transaction_date) - julianday(?)) <= 2
                          AND deleted_at IS NULL
                        ORDER BY id DESC LIMIT 1
                    """, (float(amount), f"%{clean_person}%", tx_date))
                else:
                    cursor.execute("""
                        SELECT * FROM transactions 
                        WHERE abs(amount - ?) < 0.01 
                          AND lower(person_name) LIKE ?
                          AND deleted_at IS NULL
                        ORDER BY id DESC LIMIT 1
                    """, (float(amount), f"%{clean_person}%"))
                row = cursor.fetchone()
                if row:
                    res = dict(row)
                    res['match_reason'] = f"same amount ₹{amount:.0f} to {res.get('person_name')}"
                    return res
        return None


# --- Top Payees & Daily Series ---

def get_top_payees(limit: int = 5, year: int = None, month: int = None):
    """Fetches top payees by total spent."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT 
                person_name,
                SUM(amount) as total_amount,
                COUNT(*) as count,
                MAX(category) as primary_category
            FROM transactions
            WHERE transaction_type = 'SENT' 
              AND person_name IS NOT NULL 
              AND TRIM(person_name) != ''
              AND person_name != 'Unknown'
              AND deleted_at IS NULL
        """
        params = []
        if year and month:
            query += " AND strftime('%Y-%m', transaction_date) = ?"
            params.append(f"{year:04d}-{month:02d}")
        elif year:
            query += " AND strftime('%Y', transaction_date) = ?"
            params.append(str(year))
            
        query += " GROUP BY person_name ORDER BY total_amount DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def get_daily_spend_series(year: int, month: int):
    """Returns daily spending and income series for a month for charts and heatmaps."""
    month_str = f"{year:04d}-{month:02d}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                transaction_date as date,
                SUM(CASE WHEN transaction_type = 'SENT' THEN amount ELSE 0 END) as spent,
                SUM(CASE WHEN transaction_type = 'RECEIVED' THEN amount ELSE 0 END) as received,
                COUNT(*) as count
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND deleted_at IS NULL
            GROUP BY transaction_date
            ORDER BY transaction_date ASC
        """, (month_str,))
        return [dict(row) for row in cursor.fetchall()]

def get_month_comparison_stats(year: int, month: int):
    """Calculates current month totals and previous month totals for month-over-month deltas."""
    current_summary = get_monthly_summary(year, month)
    
    # Previous month calculation
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1
        
    prev_summary = get_monthly_summary(prev_year, prev_month)
    
    def calc_delta(curr, prev):
        if prev > 0:
            pct = ((curr - prev) / prev) * 100
            diff = curr - prev
            return {'diff': diff, 'pct': round(pct, 1), 'direction': 'up' if diff > 0 else ('down' if diff < 0 else 'flat')}
        elif curr > 0:
            return {'diff': curr, 'pct': 100.0, 'direction': 'up'}
        return {'diff': 0.0, 'pct': 0.0, 'direction': 'flat'}

    return {
        'current': current_summary,
        'previous': prev_summary,
        'prev_period': f"{prev_year:04d}-{prev_month:02d}",
        'spent_delta': calc_delta(current_summary['total_sent'], prev_summary['total_sent']),
        'received_delta': calc_delta(current_summary['total_received'], prev_summary['total_received']),
        'net_delta': calc_delta(current_summary['net_savings'], prev_summary['net_savings'])
    }

def get_transactions_paginated(page: int = 1, page_size: int = 25, search: str = None, category: str = None, tx_type: str = None, year: int = None, month: int = None):
    """Fetches paginated transactions with optional filters."""
    conditions = ["deleted_at IS NULL"]
    params = []
    
    if search and search.strip():
        s = f"%{search.strip()}%"
        conditions.append("(person_name LIKE ? OR category LIKE ? OR reference_number LIKE ? OR payment_app LIKE ?)")
        params.extend([s, s, s, s])
        
    if category and category.strip() and category.lower() != 'all':
        conditions.append("category = ?")
        params.append(category.strip())
        
    if tx_type and tx_type.strip() and tx_type.upper() in ('SENT', 'RECEIVED', 'TRANSFER'):
        conditions.append("transaction_type = ?")
        params.append(tx_type.upper())
        
    if year and month:
        conditions.append("strftime('%Y-%m', transaction_date) = ?")
        params.append(f"{year:04d}-{month:02d}")
    elif year:
        conditions.append("strftime('%Y', transaction_date) = ?")
        params.append(str(year))
        
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Count total matching
        cursor.execute(f"SELECT COUNT(*) as total FROM transactions {where_clause}", params)
        total_count = cursor.fetchone()['total']
        
        # Paginated items
        offset = (page - 1) * page_size
        paginated_params = params + [page_size, offset]
        cursor.execute(f"""
            SELECT * FROM transactions 
            {where_clause}
            ORDER BY occurred_at DESC, created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """, paginated_params)
        items = [dict(r) for r in cursor.fetchall()]
        
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        return {
            'transactions': items,
            'total_count': total_count,
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages
        }

def get_contact_ledger():
    """Aggregates all transactions by contact/payee, normalizing names and showing net balance."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                person_name,
                COUNT(*) as total_transactions,
                SUM(CASE WHEN transaction_type = 'SENT' THEN amount ELSE 0 END) as total_sent,
                SUM(CASE WHEN transaction_type = 'RECEIVED' THEN amount ELSE 0 END) as total_received,
                MAX(transaction_date) as last_transaction_date,
                MAX(category) as primary_category
            FROM transactions
            WHERE person_name IS NOT NULL AND TRIM(person_name) != '' AND person_name != 'Unknown' AND deleted_at IS NULL
            GROUP BY lower(TRIM(person_name))
            ORDER BY (SUM(CASE WHEN transaction_type = 'SENT' THEN amount ELSE 0 END) + SUM(CASE WHEN transaction_type = 'RECEIVED' THEN amount ELSE 0 END)) DESC
        """)
        rows = cursor.fetchall()
        contacts = []
        for r in rows:
            sent = float(r['total_sent'] or 0.0)
            received = float(r['total_received'] or 0.0)
            contacts.append({
                'name': r['person_name'],
                'tx_count': r['total_transactions'],
                'total_sent': sent,
                'total_received': received,
                'net_balance': received - sent, # Positive means they gave you more than you gave them
                'last_date': r['last_transaction_date'],
                'category': r['primary_category'] or 'General'
            })
        return contacts



