from database.queries import get_balance_setting, update_balance_setting
from database.models import Transaction, TransactionSummary
from database.db import get_db_connection
from utils.dates import get_current_time_in_tz

def update_balance_for_transaction(transaction: Transaction) -> Transaction:
    """
    Calculates the new balance based on the transaction type and amount.
    Updates the settings table and populates balance_before and balance_after.
    """
    current_balance = get_balance_setting()
    transaction.balance_before = current_balance
    
    if transaction.transaction_type == 'SENT':
        new_balance = current_balance - transaction.amount
    elif transaction.transaction_type == 'RECEIVED':
        new_balance = current_balance + transaction.amount
    else:
        # If unknown, do not alter balance
        new_balance = current_balance
        
    transaction.balance_after = new_balance
    update_balance_setting(new_balance)
    return transaction

def get_today_summary() -> TransactionSummary:
    """Calculates summary of today's transactions."""
    today = get_current_time_in_tz().date()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT transaction_type, amount FROM transactions WHERE transaction_date = ?", (today,))
        rows = cursor.fetchall()
        
        summary = TransactionSummary()
        summary.current_balance = get_balance_setting()
        summary.transaction_count = len(rows)
        
        for row in rows:
            if row['transaction_type'] == 'SENT':
                summary.total_sent += row['amount']
            elif row['transaction_type'] == 'RECEIVED':
                summary.total_received += row['amount']
                
        summary.net_change = summary.total_received - summary.total_sent
        return summary

def recalculate_all_balances() -> float:
    """
    Recalculates balance_before and balance_after for all transactions in chronological order.
    Updates the settings table with the final current balance and returns it.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Get initial balance
        cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
        row = cursor.fetchone()
        running_balance = float(row['value']) if row else 0.0
        
        # Fetch all transactions in chronological order
        cursor.execute("SELECT id, transaction_type, amount FROM transactions ORDER BY transaction_date ASC, created_at ASC, id ASC")
        txs = cursor.fetchall()
        
        for tx in txs:
            bal_before = running_balance
            if tx['transaction_type'] == 'SENT':
                running_balance -= tx['amount']
            elif tx['transaction_type'] == 'RECEIVED':
                running_balance += tx['amount']
            bal_after = running_balance
            
            cursor.execute(
                "UPDATE transactions SET balance_before = ?, balance_after = ? WHERE id = ?",
                (bal_before, bal_after, tx['id'])
            )
            
        # Update current balance in settings
        cursor.execute(
            "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'current_balance'",
            (str(running_balance),)
        )
        conn.commit()
        
    try:
        from services.backup_service import export_database_to_json
        export_database_to_json()
    except Exception:
        pass
        
    return running_balance

def set_explicit_balance(new_balance: float) -> float:
    """
    Sets the current balance to new_balance, updates initial_balance anchor accordingly,
    and recalculates all transaction balance records so everything remains completely consistent.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT transaction_type, amount FROM transactions ORDER BY transaction_date ASC, created_at ASC, id ASC")
        txs = cursor.fetchall()
        net_delta = 0.0
        for tx in txs:
            if tx['transaction_type'] == 'SENT':
                net_delta -= tx['amount']
            elif tx['transaction_type'] == 'RECEIVED':
                net_delta += tx['amount']
        
        calc_initial = new_balance - net_delta
        cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('initial_balance', ?, CURRENT_TIMESTAMP)", (str(calc_initial),))
        conn.commit()
    
    return recalculate_all_balances()

