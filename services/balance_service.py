from decimal import Decimal
from database.queries import get_balance_setting, update_balance_setting
from database.models import Transaction, TransactionSummary
from database.db import get_db_connection, LEDGER_LOCK
from utils.dates import get_current_time_in_tz
from utils.validation import parse_decimal_amount, CENT
from config import logger

def update_balance_for_transaction(transaction: Transaction) -> Transaction:
    """
    Calculates the new balance based on the transaction type and amount using Decimal arithmetic.
    Updates the settings table and populates balance_before and balance_after.
    """
    with LEDGER_LOCK:
        curr_float = get_balance_setting()
        current_balance = Decimal(str(round(curr_float, 2))).quantize(CENT)
        amount = parse_decimal_amount(transaction.amount, allow_zero=False)
        
        transaction.balance_before = float(current_balance)
        
        if transaction.transaction_type == 'SENT':
            new_balance = current_balance - amount
        elif transaction.transaction_type == 'RECEIVED':
            new_balance = current_balance + amount
        else:
            # If unknown, do not alter balance
            new_balance = current_balance
            
        final_bal = float(new_balance.quantize(CENT))
        transaction.balance_after = final_bal
        update_balance_setting(final_bal)
        return transaction

def get_today_summary() -> TransactionSummary:
    """Calculates summary of today's transactions (live rows only)."""
    today = get_current_time_in_tz().date()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT transaction_type, amount FROM transactions WHERE transaction_date = ? AND deleted_at IS NULL", (today,))
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

def get_overall_summary() -> TransactionSummary:
    """Calculates summary across ALL live transactions."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT transaction_type, amount FROM transactions WHERE deleted_at IS NULL")
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

def resequence_transaction_ids() -> None:
    """
    DISCONTINUED: Stable permanent IDs are now maintained.
    Gaps in sequence are accepted and preserved to avoid shifting IDs across backups.
    """
    logger.debug("resequence_transaction_ids called — skipped to preserve stable permanent transaction IDs.")
    return

def recalculate_all_balances() -> float:
    """
    Recalculates balance_before and balance_after for all live transactions in chronological order.
    Uses Decimal arithmetic rounded to 2 decimals.
    Updates the settings table with the final derived current balance and returns it.
    """
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Get initial balance anchor
            cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
            row = cursor.fetchone()
            init_val = row['value'] if row and row['value'] is not None else '0.0'
            try:
                running_balance = Decimal(str(init_val)).quantize(CENT)
            except Exception:
                running_balance = Decimal('0.00')
            
            # Fetch all live transactions in chronological order
            cursor.execute("SELECT id, transaction_type, amount FROM transactions WHERE deleted_at IS NULL ORDER BY transaction_date ASC, created_at ASC, id ASC")
            txs = cursor.fetchall()
            
            for tx in txs:
                bal_before = running_balance
                try:
                    tx_amt = Decimal(str(tx['amount'])).quantize(CENT)
                except Exception:
                    tx_amt = Decimal('0.00')
                    
                if tx['transaction_type'] == 'SENT':
                    running_balance = running_balance - tx_amt
                elif tx['transaction_type'] == 'RECEIVED':
                    running_balance = running_balance + tx_amt
                bal_after = running_balance
                
                cursor.execute(
                    "UPDATE transactions SET balance_before = ?, balance_after = ? WHERE id = ?",
                    (float(bal_before), float(bal_after), tx['id'])
                )
                
            # Update current balance in settings
            final_float = float(running_balance.quantize(CENT))
            cursor.execute(
                "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'current_balance'",
                (str(final_float),)
            )
            conn.commit()
            
        try:
            from services.backup_service import export_database_to_json
            export_database_to_json()
        except Exception as e:
            logger.debug(f"JSON export notice during balance recalculation: {e}")
            
        return final_float

def set_explicit_balance(new_balance: float) -> float:
    """
    Sets the current balance to new_balance, updates initial_balance anchor accordingly,
    and recalculates all transaction balance records so everything remains completely consistent.
    Runs under LEDGER_LOCK with Decimal precision.
    """
    with LEDGER_LOCK:
        dec_new = parse_decimal_amount(new_balance, allow_zero=True)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT transaction_type, amount FROM transactions WHERE deleted_at IS NULL ORDER BY transaction_date ASC, created_at ASC, id ASC")
            txs = cursor.fetchall()
            net_delta = Decimal('0.00')
            for tx in txs:
                try:
                    amt = Decimal(str(tx['amount'])).quantize(CENT)
                except Exception:
                    amt = Decimal('0.00')
                if tx['transaction_type'] == 'SENT':
                    net_delta -= amt
                elif tx['transaction_type'] == 'RECEIVED':
                    net_delta += amt
            
            calc_initial = dec_new - net_delta
            initial_float = float(calc_initial.quantize(CENT))
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('initial_balance', ?, CURRENT_TIMESTAMP)", (str(initial_float),))
            conn.commit()
        
        return recalculate_all_balances()
