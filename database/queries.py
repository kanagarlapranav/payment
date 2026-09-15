from database.models import Transaction
from database.db import get_db_connection
from datetime import datetime
from config import logger

def insert_transaction(t: Transaction) -> int:
    """Inserts a new transaction into the database."""
    category = getattr(t, 'category', 'General') or 'General'
    query = '''
        INSERT INTO transactions (
            transaction_type, amount, person_name, sender_name, recipient_name,
            upi_id, phone_number, transaction_date, transaction_time, reference_number,
            transaction_id, payment_app, bank_name, bank_account, payment_status,
            category, balance_before, balance_after, ocr_text, original_image_path,
            telegram_message_id, telegram_chat_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''
    
    values = (
        t.transaction_type, t.amount, t.person_name, t.sender_name, t.recipient_name,
        t.upi_id, t.phone_number, t.transaction_date, t.transaction_time, t.reference_number,
        t.transaction_id, t.payment_app, t.bank_name, t.bank_account, t.payment_status,
        category, t.balance_before, t.balance_after, t.ocr_text, t.original_image_path,
        t.telegram_message_id, t.telegram_chat_id
    )
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, values)
        conn.commit()
        return cursor.lastrowid

def get_transaction_by_reference(reference_number: str):
    """Fetches a transaction by its reference number."""
    if not reference_number:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE reference_number = ?", (reference_number,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_recent_transactions(limit: int = 10):
    """Fetches recent transactions ordered by transaction date and creation date."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY transaction_date DESC, created_at DESC, id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_transactions_by_date(target_date):
    """Fetches transactions for a specific date."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE transaction_date = ?", (target_date,))
        return [dict(row) for row in cursor.fetchall()]

def get_all_transactions():
    """Fetches all transactions for export."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY transaction_date DESC, created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

def get_all_transactions_asc():
    """Fetches all transactions in ascending order (oldest first, ID #1 first)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY transaction_date ASC, created_at ASC, id ASC")
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
    conditions = []
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
        "date_desc": "transaction_date DESC, created_at DESC, id DESC",
        "date_asc": "transaction_date ASC, created_at ASC, id ASC",
        "amount_desc": "amount DESC, transaction_date DESC",
        "amount_asc": "amount ASC, transaction_date DESC",
        "created_desc": "created_at DESC, id DESC"
    }
    order_clause = sort_map.get(sort_by, "transaction_date DESC, created_at DESC")
    
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
            WHERE strftime('%Y-%m', transaction_date) = ?
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
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT' AND person_name != ''
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
    """Fetches a transaction by its ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_transaction(tx_id: int, updates: dict) -> bool:
    """Updates specific fields of a transaction."""
    if not updates:
        return False
    set_clause = ", ".join([f"{k} = ?" for k in updates.keys()]) + ", updated_at = CURRENT_TIMESTAMP"
    values = list(updates.values()) + [tx_id]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE transactions SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cursor.rowcount > 0

def delete_transaction(tx_id: int) -> bool:
    """Deletes a transaction by its ID, resequences remaining IDs consecutively, and recalculates balances."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

    if deleted:
        try:
            from services.balance_service import resequence_transaction_ids, recalculate_all_balances
            resequence_transaction_ids()
            recalculate_all_balances()
        except Exception as e:
            logger.error(f"Error during post-delete resequence/recalculate: {e}")

    return deleted

def update_balance_setting(new_balance: float):
    """Updates the current balance in the settings table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('current_balance', ?, CURRENT_TIMESTAMP)", (str(new_balance),))
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
    """Sets the monthly budget limit in the settings table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('monthly_budget', ?, CURRENT_TIMESTAMP)", (str(amount),))
        conn.commit()

def get_monthly_spending(year: int, month: int) -> float:
    """Gets the total SENT amount for a given month."""
    month_str = f"{year:04d}-{month:02d}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? AND transaction_type = 'SENT'
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
            WHERE strftime('%Y-%m', transaction_date) = ?
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
            WHERE transaction_date = ?
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
            WHERE transaction_date = ?
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
            WHERE lower(person_name) LIKE '%vikraman%' 
               OR lower(person_name) LIKE '%cafeteria%' 
               OR lower(person_name) LIKE '%canteen%'
               OR lower(upi_id) LIKE '%vikraman%'
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


