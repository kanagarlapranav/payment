"""
Recurring payments management service for subscriptions, bills, SIPs, and periodic dues.
Provides robust calculation of due dates, ledger integration on payment confirmation,
skip cycle logic, and monthly projection totals.
"""

from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Optional, Tuple
from database.db import get_db_connection, LEDGER_LOCK
from database.models import Transaction
from database.queries import insert_transaction_with_balance, increment_revision_and_mark_dirty
from utils.dates import get_current_time_in_tz, utc_now_iso
from utils.validation import parse_decimal_amount, validate_string_length
from config import logger

def add_months(sourcedate: date, months: int) -> date:
    """Adds N months to a date, preserving end of month behavior safely."""
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def calculate_next_due_date(from_date: date, frequency: str = "MONTHLY", interval: int = 1) -> date:
    """Calculates subsequent due date based on frequency and interval."""
    freq = (frequency or "MONTHLY").upper()
    interval = max(1, int(interval))
    
    if freq == "DAILY":
        return from_date + timedelta(days=interval)
    elif freq == "WEEKLY":
        return from_date + timedelta(weeks=interval)
    elif freq == "YEARLY":
        return add_months(from_date, 12 * interval)
    else:  # MONTHLY (default)
        return add_months(from_date, interval)

def add_recurring_payment(
    payee_name: str,
    amount: float,
    category: str = "Bills & Utilities",
    transaction_type: str = "SENT",
    frequency: str = "MONTHLY",
    interval_value: int = 1,
    start_date: Optional[date] = None,
    reminder_days_before: int = 1,
    notes: str = ""
) -> int:
    """Creates a new active recurring payment rule."""
    dec_amount = float(parse_decimal_amount(amount, allow_zero=False))
    clean_payee = validate_string_length(payee_name, max_length=120, field_name="Payee name")
    clean_category = validate_string_length(category or "Bills & Utilities", max_length=100, field_name="Category")
    clean_type = transaction_type.upper() if transaction_type in ("SENT", "RECEIVED") else "SENT"
    clean_freq = frequency.upper() if frequency in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") else "MONTHLY"
    
    if not start_date:
        start_date = get_current_time_in_tz().date()
    elif isinstance(start_date, str):
        start_date = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
        
    next_due = start_date
    day_num = start_date.day
    now_iso = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(recurring_payments)")
            cols = [r[1] for r in cursor.fetchall()]
            
            if "day_of_month" in cols:
                cursor.execute("""
                    INSERT INTO recurring_payments (
                        payee_name, amount, category, transaction_type, frequency,
                        interval_value, start_date, next_due_date, last_paid_date,
                        reminder_days_before, auto_log, status, notes, day_of_month, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 'ACTIVE', ?, ?, ?, ?)
                """, (
                    clean_payee, dec_amount, clean_category, clean_type, clean_freq,
                    interval_value, str(start_date), str(next_due),
                    reminder_days_before, notes or "", day_num, now_iso, now_iso
                ))
            else:
                cursor.execute("""
                    INSERT INTO recurring_payments (
                        payee_name, amount, category, transaction_type, frequency,
                        interval_value, start_date, next_due_date, last_paid_date,
                        reminder_days_before, auto_log, status, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 'ACTIVE', ?, ?, ?)
                """, (
                    clean_payee, dec_amount, clean_category, clean_type, clean_freq,
                    interval_value, str(start_date), str(next_due),
                    reminder_days_before, notes or "", now_iso, now_iso
                ))
            new_id = cursor.lastrowid
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return new_id

def get_all_recurring(include_inactive: bool = False) -> List[Dict]:
    """Fetches all recurring payment rules."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if include_inactive:
            cursor.execute("SELECT * FROM recurring_payments ORDER BY next_due_date ASC, id ASC")
        else:
            cursor.execute("SELECT * FROM recurring_payments WHERE status = 'ACTIVE' ORDER BY next_due_date ASC, id ASC")
        return [dict(r) for r in cursor.fetchall()]

def get_recurring_by_id(rec_id: int) -> Optional[Dict]:
    """Retrieves a recurring payment by its ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM recurring_payments WHERE id = ?", (rec_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_upcoming_recurring(days_ahead: int = 30) -> List[Dict]:
    """Fetches active recurring payments due within the next N days."""
    today = get_current_time_in_tz().date()
    max_due = today + timedelta(days=days_ahead)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM recurring_payments
            WHERE status = 'ACTIVE' AND next_due_date <= ?
            ORDER BY next_due_date ASC, id ASC
        """, (str(max_due),))
        return [dict(r) for r in cursor.fetchall()]

def mark_recurring_paid(rec_id: int, paid_date: Optional[date] = None) -> Tuple[int, date]:
    """
    Marks a recurring payment as paid:
    1. Inserts a Transaction into the ledger.
    2. Advances next_due_date to the next cycle.
    3. Updates last_paid_date and revision under LEDGER_LOCK.
    Returns (created_transaction_id, new_next_due_date).
    """
    if not paid_date:
        paid_date = get_current_time_in_tz().date()
        
    with LEDGER_LOCK:
        rec = get_recurring_by_id(rec_id)
        if not rec:
            raise ValueError(f"Recurring payment #{rec_id} not found")
            
        import uuid
        unique_ref = f"REC-{rec_id}-{uuid.uuid4().hex[:6].upper()}"
        t = Transaction(
            amount=rec['amount'],
            transaction_type=rec['transaction_type'] or 'SENT',
            person_name=rec['payee_name'],
            category=rec['category'] or 'Bills & Utilities',
            transaction_date=str(paid_date),
            reference_number=unique_ref
        )
        tx_id = insert_transaction_with_balance(t)
        
        # Calculate next due date
        current_due = datetime.strptime(str(rec['next_due_date'])[:10], "%Y-%m-%d").date()
        new_due = calculate_next_due_date(current_due, rec['frequency'], rec['interval_value'])
        
        # If new_due is still in the past compared to paid_date, advance further
        while new_due <= paid_date:
            new_due = calculate_next_due_date(new_due, rec['frequency'], rec['interval_value'])
            
        now_iso = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recurring_payments
                SET last_paid_date = ?, next_due_date = ?, updated_at = ?
                WHERE id = ?
            """, (str(paid_date), str(new_due), now_iso, rec_id))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            
        return tx_id, new_due

def skip_recurring_due(rec_id: int) -> date:
    """
    Skips the current cycle of a recurring payment without logging an expense.
    Advances next_due_date by one interval cycle.
    """
    with LEDGER_LOCK:
        rec = get_recurring_by_id(rec_id)
        if not rec:
            raise ValueError(f"Recurring payment #{rec_id} not found")
            
        current_due = datetime.strptime(str(rec['next_due_date'])[:10], "%Y-%m-%d").date()
        new_due = calculate_next_due_date(current_due, rec['frequency'], rec['interval_value'])
        now_iso = utc_now_iso()
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recurring_payments
                SET next_due_date = ?, updated_at = ?
                WHERE id = ?
            """, (str(new_due), now_iso, rec_id))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return new_due

def update_recurring_status(rec_id: int, new_status: str) -> bool:
    """Updates status of recurring payment (ACTIVE, PAUSED, CANCELLED)."""
    norm = new_status.upper()
    if norm not in ("ACTIVE", "PAUSED", "CANCELLED"):
        raise ValueError("Status must be ACTIVE, PAUSED, or CANCELLED")
    now_iso = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE recurring_payments SET status = ?, updated_at = ? WHERE id = ?", (norm, now_iso, rec_id))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return cursor.rowcount > 0

def delete_recurring_payment(rec_id: int) -> bool:
    """Permanently deletes a recurring payment rule."""
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM recurring_payments WHERE id = ?", (rec_id,))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return cursor.rowcount > 0

def get_recurring_monthly_total() -> float:
    """
    Computes total projected monthly expenditure from all active recurring payments.
    Converts DAILY (*30), WEEKLY (*4.33), YEARLY (/12), and MONTHLY (/interval).
    """
    active_items = get_all_recurring(include_inactive=False)
    total_monthly = 0.0
    for r in active_items:
        amt = float(r.get('amount', 0))
        freq = (r.get('frequency') or 'MONTHLY').upper()
        interval = max(1, int(r.get('interval_value') or 1))
        
        if freq == 'DAILY':
            monthly_equiv = amt * 30.0 / interval
        elif freq == 'WEEKLY':
            monthly_equiv = amt * (52.0 / 12.0) / interval
        elif freq == 'YEARLY':
            monthly_equiv = (amt / 12.0) / interval
        else:  # MONTHLY
            monthly_equiv = amt / interval
            
        if r.get('transaction_type') == 'SENT':
            total_monthly += monthly_equiv
            
    return round(total_monthly, 2)
