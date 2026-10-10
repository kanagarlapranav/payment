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
    notes: str = "",
    workspace_id: Optional[str] = None
) -> int:
    """Creates a new active recurring payment rule with workspace scoping."""
    if not workspace_id or not str(workspace_id).strip():
        raise ValueError("workspace_id is required to add recurring payment")
    ws_id = str(workspace_id).strip()

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
                        workspace_id, payee_name, amount, category, transaction_type, frequency,
                        interval_value, start_date, next_due_date, last_paid_date,
                        reminder_days_before, auto_log, status, notes, day_of_month, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 'ACTIVE', ?, ?, ?, ?)
                """, (
                    ws_id, clean_payee, dec_amount, clean_category, clean_type, clean_freq,
                    interval_value, str(start_date), str(next_due),
                    reminder_days_before, notes or "", day_num, now_iso, now_iso
                ))
            else:
                cursor.execute("""
                    INSERT INTO recurring_payments (
                        workspace_id, payee_name, amount, category, transaction_type, frequency,
                        interval_value, start_date, next_due_date, last_paid_date,
                        reminder_days_before, auto_log, status, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 'ACTIVE', ?, ?, ?)
                """, (
                    ws_id, clean_payee, dec_amount, clean_category, clean_type, clean_freq,
                    interval_value, str(start_date), str(next_due),
                    reminder_days_before, notes or "", now_iso, now_iso
                ))
            new_id = cursor.lastrowid
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return new_id

def get_all_recurring(include_inactive: bool = False, workspace_id: Optional[str] = None) -> List[Dict]:
    """Fetches recurring payment rules filtered by workspace."""
    from database.queries import get_default_workspace_id
    default_ws = get_default_workspace_id()
    ws_id = str(workspace_id) if workspace_id else default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if include_inactive:
            cursor.execute(f"SELECT * FROM recurring_payments WHERE {ws_filter} ORDER BY next_due_date ASC, id ASC", (ws_id,))
        else:
            cursor.execute(f"SELECT * FROM recurring_payments WHERE status = 'ACTIVE' AND {ws_filter} ORDER BY next_due_date ASC, id ASC", (ws_id,))
        return [dict(r) for r in cursor.fetchall()]

def get_recurring_by_id(rec_id: int, workspace_id: Optional[str] = None) -> Optional[Dict]:
    """Retrieves a recurring payment by its ID with optional workspace scoping."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if workspace_id:
            from database.queries import get_default_workspace_id
            default_ws = get_default_workspace_id()
            ws_id = str(workspace_id)
            ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"
            cursor.execute(f"SELECT * FROM recurring_payments WHERE id = ? AND {ws_filter}", (rec_id, ws_id))
        else:
            cursor.execute("SELECT * FROM recurring_payments WHERE id = ?", (rec_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_upcoming_recurring(days_ahead: int = 30, workspace_id: Optional[str] = None) -> List[Dict]:
    """Fetches active recurring payments due within next N days with workspace scoping."""
    today = get_current_time_in_tz().date()
    max_due = today + timedelta(days=days_ahead)
    from database.queries import get_default_workspace_id
    default_ws = get_default_workspace_id()
    ws_id = str(workspace_id) if workspace_id else default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM recurring_payments
            WHERE status = 'ACTIVE' AND next_due_date <= ? AND {ws_filter}
            ORDER BY next_due_date ASC, id ASC
        """, (str(max_due), ws_id))
        return [dict(r) for r in cursor.fetchall()]

def mark_recurring_paid(rec_id: int, paid_date: Optional[date] = None, workspace_id: Optional[str] = None) -> Tuple[int, date]:
    """
    Marks a recurring payment as paid atomically:
    1. Checks and locks the recurring rule.
    2. Performs conditional UPDATE checking current next_due_date to prevent race double-logging.
    3. Inserts a Transaction into the ledger for that workspace.
    Returns (created_transaction_id, new_next_due_date).
    """
    if not paid_date:
        paid_date = get_current_time_in_tz().date()
        
    with LEDGER_LOCK:
        rec = get_recurring_by_id(rec_id, workspace_id=workspace_id)
        if not rec:
            raise ValueError(f"Recurring payment #{rec_id} not found")

        current_due = datetime.strptime(str(rec['next_due_date'])[:10], "%Y-%m-%d").date()
        new_due = calculate_next_due_date(current_due, rec['frequency'], rec['interval_value'])
        while new_due <= paid_date:
            new_due = calculate_next_due_date(new_due, rec['frequency'], rec['interval_value'])

        now_iso = utc_now_iso()
        ws_id = rec.get('workspace_id') or workspace_id
        if not ws_id:
            raise ValueError("workspace_id is required to mark recurring paid")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Atomic conditional update prevents double-click double logging
            cursor.execute("""
                UPDATE recurring_payments
                SET last_paid_date = ?, next_due_date = ?, updated_at = ?
                WHERE id = ? AND next_due_date = ?
            """, (str(paid_date), str(new_due), now_iso, rec_id, str(rec['next_due_date'])))
            if cursor.rowcount == 0:
                raise ValueError(f"Recurring payment #{rec_id} was already updated or paid.")
            increment_revision_and_mark_dirty(conn)
            conn.commit()

        import uuid
        unique_ref = f"REC-{rec_id}-{uuid.uuid4().hex[:6].upper()}"
        t = Transaction(
            amount=rec['amount'],
            transaction_type=rec['transaction_type'] or 'SENT',
            person_name=rec['payee_name'],
            category=rec['category'] or 'Bills & Utilities',
            transaction_date=str(paid_date),
            reference_number=unique_ref,
            workspace_id=ws_id
        )
        try:
            tx_id = insert_transaction_with_balance(t)
        except Exception:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE recurring_payments
                    SET last_paid_date = ?, next_due_date = ?, updated_at = ?
                    WHERE id = ?
                """, (rec.get('last_paid_date'), str(rec['next_due_date']), now_iso, rec_id))
                increment_revision_and_mark_dirty(conn)
                conn.commit()
            raise

        return tx_id, new_due

def skip_recurring_due(rec_id: int, workspace_id: Optional[str] = None) -> date:
    """
    Skips the current cycle of a recurring payment without logging an expense.
    Advances next_due_date by one interval cycle.
    """
    with LEDGER_LOCK:
        rec = get_recurring_by_id(rec_id, workspace_id=workspace_id)
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
                WHERE id = ? AND next_due_date = ?
            """, (str(new_due), now_iso, rec_id, str(rec['next_due_date'])))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return new_due

def update_recurring_status(rec_id: int, new_status: str, workspace_id: Optional[str] = None) -> bool:
    """Updates status of recurring payment (ACTIVE, PAUSED, CANCELLED) with workspace scoping."""
    norm = new_status.upper()
    if norm not in ("ACTIVE", "PAUSED", "CANCELLED"):
        raise ValueError("Status must be ACTIVE, PAUSED, or CANCELLED")
    now_iso = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if workspace_id:
                from database.queries import get_default_workspace_id
                default_ws = get_default_workspace_id()
                ws_id = str(workspace_id)
                ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"
                cursor.execute(f"UPDATE recurring_payments SET status = ?, updated_at = ? WHERE id = ? AND {ws_filter}", (norm, now_iso, rec_id, ws_id))
            else:
                cursor.execute("UPDATE recurring_payments SET status = ?, updated_at = ? WHERE id = ?", (norm, now_iso, rec_id))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return cursor.rowcount > 0

def delete_recurring_payment(rec_id: int, workspace_id: Optional[str] = None) -> bool:
    """Permanently deletes a recurring payment rule with workspace scoping."""
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if workspace_id:
                from database.queries import get_default_workspace_id
                default_ws = get_default_workspace_id()
                ws_id = str(workspace_id)
                ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"
                cursor.execute(f"DELETE FROM recurring_payments WHERE id = ? AND {ws_filter}", (rec_id, ws_id))
            else:
                cursor.execute("DELETE FROM recurring_payments WHERE id = ?", (rec_id,))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return cursor.rowcount > 0

def get_recurring_monthly_total(workspace_id: Optional[str] = None) -> float:
    """
    Computes total projected monthly expenditure from all active recurring payments for a workspace.
    Converts DAILY (*30), WEEKLY (*4.33), YEARLY (/12), and MONTHLY (/interval).
    """
    active_items = get_all_recurring(include_inactive=False, workspace_id=workspace_id)
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
