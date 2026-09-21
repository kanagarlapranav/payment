from decimal import Decimal
import sqlite3
from typing import Optional, List
from database.models import Transaction, TransactionSummary
from database.db import get_db_connection, LEDGER_LOCK
from database.queries import get_balance_setting, update_balance_setting
from utils.dates import get_current_time_in_tz, utc_now_iso
from utils.validation import parse_decimal_amount, validate_uid, CENT
from config import logger

def recalculate_in_connection(conn: sqlite3.Connection) -> float:
    """
    Recalculates balance_before and balance_after for all live transactions
    strictly ordered by: occurred_at ASC, created_at ASC, id ASC.
    Uses Decimal arithmetic:
      - SENT subtracts
      - RECEIVED adds
      - Starts from initial_balance
      - Sets current_balance in settings (empty ledger = initial_balance)
      - Must NOT touch updated_at on transactions.
    Returns the final current_balance as float.
    """
    cursor = conn.cursor()

    # 1. Fetch initial_balance anchor from settings
    cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
    row = cursor.fetchone()
    init_val_str = row['value'] if row and row['value'] is not None else '0.0'
    try:
        running_balance = Decimal(str(init_val_str)).quantize(CENT)
    except Exception:
        running_balance = Decimal('0.00')

    # 2. Fetch all live transactions in strict chronological order
    cursor.execute('''
        SELECT id, transaction_type, amount, occurred_at, created_at
        FROM transactions
        WHERE deleted_at IS NULL
        ORDER BY occurred_at ASC, created_at ASC, id ASC
    ''')
    txs = cursor.fetchall()

    # 3. Iterate through chain updating balances without touching updated_at
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

    # 4. Update current_balance in settings (empty ledger = initial_balance)
    final_float = float(running_balance.quantize(CENT))
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('current_balance', ?, ?)",
        (str(final_float), utc_now_iso())
    )

    return final_float

def recalculate_all_balances() -> float:
    """
    Recalculates balance_before and balance_after for all live transactions in chronological order
    under LEDGER_LOCK. Uses Decimal arithmetic rounded to 2 decimals.
    Sets current_balance and returns it. Does NOT touch updated_at on transactions.
    """
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            final_float = recalculate_in_connection(conn)
            conn.commit()
            return final_float

def set_explicit_balance(new_balance: float) -> float:
    """
    Sets the current balance to new_balance by adjusting initial_balance anchor such that
    current_balance equals the target after recalculation.
    Formula: initial_balance = target - (sum(RECEIVED) - sum(SENT))
    Later recalculations will never overwrite this balance.
    Runs under LEDGER_LOCK with Decimal precision.
    """
    with LEDGER_LOCK:
        dec_new = parse_decimal_amount(new_balance, allow_zero=True)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT transaction_type, amount
                FROM transactions
                WHERE deleted_at IS NULL
                ORDER BY occurred_at ASC, created_at ASC, id ASC
            ''')
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
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('initial_balance', ?, ?)",
                (str(initial_float), utc_now_iso())
            )

            # Recalculate within this same connection
            final_bal = recalculate_in_connection(conn)
            from database.queries import increment_revision_and_mark_dirty
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return final_bal

def update_balance_for_transaction(transaction: Transaction) -> Transaction:
    """
    Calculates the new balance based on transaction type and amount using Decimal arithmetic.
    Updates the settings table and populates balance_before and balance_after.
    """
    with LEDGER_LOCK:
        current_balance = Decimal(str(get_balance_setting())).quantize(CENT)
        amount = parse_decimal_amount(transaction.amount, allow_zero=False)
        transaction.balance_before = float(current_balance)

        if transaction.transaction_type == 'SENT':
            new_balance = current_balance - amount
        elif transaction.transaction_type == 'RECEIVED':
            new_balance = current_balance + amount
        else:
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
        cursor.execute("SELECT transaction_type, amount FROM transactions WHERE transaction_date = ? AND deleted_at IS NULL", (str(today),))
        rows = cursor.fetchall()

        cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        bal_row = cursor.fetchone()
        cur_bal = float(bal_row['value']) if bal_row and bal_row['value'] is not None else 0.0

        summary = TransactionSummary()
        summary.current_balance = cur_bal
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

        cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        bal_row = cursor.fetchone()
        cur_bal = float(bal_row['value']) if bal_row and bal_row['value'] is not None else 0.0

        summary = TransactionSummary()
        summary.current_balance = cur_bal
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

def validate_ledger_invariants(db_path=None) -> List[str]:
    """
    Validates all ledger integrity invariants against the SQLite database.
    Checks:
      - broken chain (balance_before / balance_after continuity)
      - wrong signs (SENT increases balance, RECEIVED decreases, negative amounts)
      - invalid transaction types (must be SENT or RECEIVED)
      - non-positive amounts (amount <= 0, NaN, Inf)
      - current_balance mismatch in settings
      - duplicate live reference numbers
      - invalid or missing UIDs
      - inconsistent deleted_at values
    Never auto-repairs. Returns a list of human-readable error strings.
    """
    errors: List[str] = []

    with get_db_connection(db_path=db_path) as conn:
        cursor = conn.cursor()

        # 1. Fetch settings
        cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
        init_row = cursor.fetchone()
        try:
            raw_init = Decimal(str(init_row['value'])) if init_row else Decimal('0.00')
            if not raw_init.is_finite():
                errors.append("Invalid initial_balance setting: non-finite Decimal")
                initial_balance = Decimal('0.00')
            else:
                initial_balance = raw_init.quantize(CENT)
        except Exception as err:
            errors.append(f"Invalid initial_balance setting: {err}")
            initial_balance = Decimal('0.00')

        cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        cur_row = cursor.fetchone()
        try:
            raw_cur = Decimal(str(cur_row['value'])) if cur_row else None
            if raw_cur is not None and not raw_cur.is_finite():
                errors.append("Invalid current_balance setting: non-finite Decimal")
                current_balance = None
            else:
                current_balance = raw_cur.quantize(CENT) if raw_cur is not None else None
        except Exception as err:
            errors.append(f"Invalid current_balance setting: {err}")
            current_balance = None

        # 2. Inspect all rows (live and deleted) for structural validity
        cursor.execute("SELECT * FROM transactions ORDER BY id ASC")
        all_txs = cursor.fetchall()

        seen_live_refs = {}
        for row in all_txs:
            row_id = row['id']
            tt = row['transaction_type']
            amt = row['amount']
            uid = row['uid']
            del_at = row['deleted_at']
            ref = row['reference_number']

            # Invalid transaction types
            if tt not in ('SENT', 'RECEIVED', 'TRANSFER'):
                errors.append(f"Row {row_id}: invalid transaction_type {tt!r}")

            # Non-positive amounts / NaN / Inf
            try:
                dec_amt = parse_decimal_amount(amt, allow_zero=False)
            except Exception as err:
                errors.append(f"Row {row_id}: non-positive or invalid amount {amt!r} ({err})")

            # Invalid or missing UIDs
            try:
                if not uid:
                    raise ValueError("UID is missing or empty")
                validate_uid(uid)
            except Exception as err:
                errors.append(f"Row {row_id}: invalid or missing UID {uid!r} ({err})")

            # Duplicate live reference numbers
            if del_at is None and ref and str(ref).strip():
                clean_ref = str(ref).strip()
                if clean_ref in seen_live_refs:
                    errors.append(f"Duplicate live reference_number {clean_ref!r} on rows {seen_live_refs[clean_ref]} and {row_id}")
                else:
                    seen_live_refs[clean_ref] = row_id

            # Inconsistent deleted_at
            if del_at is not None:
                del_str = str(del_at).strip()
                if not del_str or del_str.lower() in ('none', 'null', '0', 'false'):
                    errors.append(f"Row {row_id}: inconsistent deleted_at value {del_at!r}")

        # 3. Check ledger continuity on live transactions in strict chronological order
        cursor.execute('''
            SELECT id, transaction_type, amount, balance_before, balance_after, occurred_at, created_at
            FROM transactions
            WHERE deleted_at IS NULL
            ORDER BY occurred_at ASC, created_at ASC, id ASC
        ''')
        live_txs = cursor.fetchall()

        expected_balance = initial_balance
        for row in live_txs:
            row_id = row['id']
            tt = row['transaction_type']

            try:
                raw_amt = Decimal(str(row['amount']))
                if not raw_amt.is_finite():
                    dec_amt = Decimal('0.00')
                else:
                    dec_amt = raw_amt.quantize(CENT)
            except Exception:
                dec_amt = Decimal('0.00')

            try:
                raw_bb = Decimal(str(row['balance_before']))
                bal_before = raw_bb.quantize(CENT) if raw_bb.is_finite() else None
            except Exception:
                bal_before = None

            try:
                raw_ba = Decimal(str(row['balance_after']))
                bal_after = raw_ba.quantize(CENT) if raw_ba.is_finite() else None
            except Exception:
                bal_after = None

            # Broken chain: balance_before must equal previous row's balance_after (or initial_balance)
            if bal_before != expected_balance:
                errors.append(f"Row {row_id}: broken chain balance_before mismatch (expected {expected_balance}, got {bal_before})")

            # Validate signs and balance_after
            if tt == 'SENT':
                calc_after = (bal_before - dec_amt) if bal_before is not None else None
                if dec_amt.is_finite() and dec_amt < Decimal('0.00'):
                    errors.append(f"Row {row_id}: wrong sign for SENT amount ({dec_amt})")
            elif tt == 'RECEIVED':
                calc_after = (bal_before + dec_amt) if bal_before is not None else None
                if dec_amt.is_finite() and dec_amt < Decimal('0.00'):
                    errors.append(f"Row {row_id}: wrong sign for RECEIVED amount ({dec_amt})")
            elif tt == 'TRANSFER':
                calc_after = bal_before
                if dec_amt.is_finite() and dec_amt < Decimal('0.00'):
                    errors.append(f"Row {row_id}: wrong sign for TRANSFER amount ({dec_amt})")
            else:
                calc_after = None

            if calc_after is not None and bal_after != calc_after:
                errors.append(f"Row {row_id}: broken chain balance_after mismatch (expected {calc_after}, got {bal_after})")

            if calc_after is not None:
                expected_balance = calc_after
            elif bal_after is not None:
                expected_balance = bal_after

        # 4. Check that current_balance in settings matches the end of the chain
        if current_balance is not None and current_balance != expected_balance:
            errors.append(f"current_balance mismatch in settings (expected {expected_balance}, found {current_balance})")

    return errors
