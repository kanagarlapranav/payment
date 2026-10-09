from decimal import Decimal
import sqlite3
from typing import Optional, List
from database.models import Transaction, TransactionSummary
from database.db import get_db_connection, LEDGER_LOCK
from database.queries import get_balance_setting, update_balance_setting
from utils.dates import get_current_time_in_tz, utc_now_iso
from utils.validation import parse_decimal_amount, validate_uid, CENT
from config import logger

def recalculate_in_connection(conn: sqlite3.Connection, workspace_id: str = None) -> float:
    """
    Recalculates balance_before and balance_after for all live transactions in the workspace
    strictly ordered by: occurred_at ASC, created_at ASC, id ASC.
    Uses Decimal arithmetic:
      - SENT subtracts
      - RECEIVED adds
      - TRANSFER keeps running_balance unchanged (bal_after == bal_before)
      - Starts from initial_balance
      - Sets current_balance in workspace_settings and settings (empty ledger = initial_balance)
      - Must NOT touch updated_at on transactions.
    Returns the final current_balance as float.
    Raises ValueError on invalid/corrupted amounts or invalid transaction types.
    """
    cursor = conn.cursor()
    from database.queries import get_default_workspace_id
    ws_id = workspace_id or get_default_workspace_id()

    # 1. Fetch initial_balance anchor from workspace_settings, fallback to settings only for default workspace
    cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'initial_balance'", (ws_id,))
    row = cursor.fetchone()
    if not row and ws_id == get_default_workspace_id():
        cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
        row = cursor.fetchone()
    init_val_str = row['value'] if row and row['value'] is not None else '0.0'
    try:
        raw_init = Decimal(str(init_val_str))
        if not raw_init.is_finite():
            raise ValueError(f"Non-finite initial_balance: {init_val_str}")
        running_balance = raw_init.quantize(CENT)
    except Exception as err:
        raise ValueError(f"Invalid initial_balance in settings: {init_val_str!r} ({err})")

    # 2. Fetch all live transactions for this workspace in strict chronological order
    default_ws = get_default_workspace_id()
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"
    cursor.execute(f'''
        SELECT id, transaction_type, amount, occurred_at, created_at
        FROM transactions
        WHERE {ws_filter}
          AND deleted_at IS NULL
        ORDER BY occurred_at ASC, created_at ASC, id ASC
    ''', (ws_id,))
    txs = cursor.fetchall()

    # 3. Iterate through chain updating balances without touching updated_at
    for tx in txs:
        bal_before = running_balance
        try:
            tx_amt = parse_decimal_amount(tx['amount'], allow_zero=False)
        except Exception as err:
            raise ValueError(f"Invalid amount in transaction {tx['id']}: {tx['amount']!r} ({err})")

        tt = tx['transaction_type']
        if tt == 'SENT':
            running_balance = running_balance - tx_amt
        elif tt == 'RECEIVED':
            running_balance = running_balance + tx_amt
        elif tt == 'TRANSFER':
            pass  # Balance remains unchanged on transfer
        else:
            raise ValueError(f"Invalid transaction type '{tt}' in transaction {tx['id']}")

        bal_after = running_balance

        cursor.execute(
            "UPDATE transactions SET balance_before = ?, balance_after = ? WHERE id = ?",
            (float(bal_before), float(bal_after), tx['id'])
        )

    # 4. Update current_balance in workspace_settings and settings (empty ledger = initial_balance)
    final_float = float(running_balance.quantize(CENT))
    now_utc = utc_now_iso()
    cursor.execute(
        "INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, 'current_balance', ?, ?)",
        (ws_id, str(final_float), now_utc)
    )
    if ws_id == get_default_workspace_id():
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('current_balance', ?, ?)",
            (str(final_float), now_utc)
        )

    return final_float

def recalculate_all_balances(workspace_id: str = None) -> float:
    """
    Recalculates balance_before and balance_after for all live transactions in chronological order
    under LEDGER_LOCK. Uses Decimal arithmetic rounded to 2 decimals.
    Sets current_balance and returns it. Does NOT touch updated_at on transactions.
    """
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            final_float = recalculate_in_connection(conn, workspace_id=workspace_id)
            conn.commit()
            return final_float

def set_explicit_balance(new_balance: float, workspace_id: str = None) -> float:
    """
    Sets the current balance to new_balance by adjusting initial_balance anchor such that
    current_balance equals the target after recalculation.
    Formula: initial_balance = target - (sum(RECEIVED) - sum(SENT))
    Later recalculations will never overwrite this balance.
    Runs under LEDGER_LOCK with Decimal precision.
    """
    from database.queries import get_default_workspace_id
    default_ws = get_default_workspace_id()
    target_ws = workspace_id or default_ws

    with LEDGER_LOCK:
        dec_new = parse_decimal_amount(new_balance, allow_zero=True, allow_negative=True)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if target_ws == default_ws else "workspace_id = ?"
            cursor.execute(f'''
                SELECT id, transaction_type, amount
                FROM transactions
                WHERE {ws_filter} AND deleted_at IS NULL
                ORDER BY occurred_at ASC, created_at ASC, id ASC
            ''', (target_ws,))
            txs = cursor.fetchall()
            net_delta = Decimal('0.00')
            for tx in txs:
                try:
                    amt = parse_decimal_amount(tx['amount'], allow_zero=False)
                except Exception as err:
                    raise ValueError(f"Invalid amount in transaction {tx['id']}: {tx['amount']!r} ({err})")
                tt = tx['transaction_type']
                if tt == 'SENT':
                    net_delta -= amt
                elif tt == 'RECEIVED':
                    net_delta += amt
                elif tt == 'TRANSFER':
                    pass
                else:
                    raise ValueError(f"Invalid transaction type '{tt}' in transaction {tx['id']}")

            calc_initial = dec_new - net_delta
            initial_float = float(calc_initial.quantize(CENT))
            now_utc = utc_now_iso()

            if target_ws:
                cursor.execute(
                    "INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, 'initial_balance', ?, ?)",
                    (target_ws, str(initial_float), now_utc)
                )

            if not target_ws or target_ws == default_ws:
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('initial_balance', ?, ?)",
                    (str(initial_float), now_utc)
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)",
                    (now_utc,)
                )

            # Recalculate within this same connection
            final_bal = recalculate_in_connection(conn, workspace_id=target_ws)
            from database.queries import increment_revision_and_mark_dirty
            increment_revision_and_mark_dirty(conn)
            conn.commit()
            return final_bal

def update_balance_for_transaction(transaction: Transaction) -> Transaction:
    """
    Pure calculation helper: populates balance_before and balance_after
    based on current_balance and transaction type without independently mutating settings.
    """
    ws_id = getattr(transaction, 'workspace_id', None)
    raw_bal = get_balance_setting(ws_id) if ws_id else get_balance_setting()
    current_balance = Decimal(str(raw_bal)).quantize(CENT)
    amount = parse_decimal_amount(transaction.amount, allow_zero=False)
    transaction.balance_before = float(current_balance)

    if transaction.transaction_type == 'SENT':
        new_balance = current_balance - amount
    elif transaction.transaction_type == 'RECEIVED':
        new_balance = current_balance + amount
    elif transaction.transaction_type == 'TRANSFER':
        new_balance = current_balance
    else:
        new_balance = current_balance

    final_bal = float(new_balance.quantize(CENT))
    transaction.balance_after = final_bal
    return transaction

def get_today_summary(workspace_id: str = None, user_id: int = None) -> TransactionSummary:
    """Calculates summary of today's transactions (live rows only) with Decimal precision, workspace isolation, and optional user filtering."""
    from database.queries import get_default_workspace_id, _build_user_filter
    today = get_current_time_in_tz().date()
    ws_id = workspace_id or get_default_workspace_id()

    default_ws = get_default_workspace_id()
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"

    conditions = ["transaction_date = ?", ws_filter, "deleted_at IS NULL"]
    params = [str(today), ws_id]

    if user_id is not None:
        from config import TELEGRAM_USER_ID
        owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else None
        if not (owner_id and int(user_id) == owner_id):
            with get_db_connection() as conn_check:
                c_check = conn_check.cursor()
                c_check.execute("SELECT id FROM workspaces WHERE chat_id = ? AND is_active = 1", (int(user_id),))
                p_r = c_check.fetchone()
                if p_r and p_r['id'] and p_r['id'] != ws_id:
                    ws_filter = "(workspace_id = ? OR workspace_id = ?)"
                    conditions = ["transaction_date = ?", ws_filter, "deleted_at IS NULL"]
                    params = [str(today), ws_id, p_r['id']]

    u_sql, u_params = _build_user_filter(user_id)
    if u_sql:
        conditions.append(u_sql)
        params.extend(u_params)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT transaction_type, amount 
            FROM transactions 
            WHERE {' AND '.join(conditions)}
        """, params)
        rows = cursor.fetchall()

        if user_id is not None:
            from config import TELEGRAM_USER_ID
            owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else None
            if owner_id and int(user_id) == owner_id:
                cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'current_balance'", (ws_id,))
                bal_row = cursor.fetchone()
                if not bal_row:
                    cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                    bal_row = cursor.fetchone()
                cur_bal = float(bal_row['value']) if bal_row and bal_row['value'] is not None else 0.0
            else:
                cursor.execute("SELECT id FROM workspaces WHERE chat_id = ? AND is_active = 1", (int(user_id),))
                p_ws_row = cursor.fetchone()
                p_ws_id = p_ws_row['id'] if p_ws_row else None

                if p_ws_id and p_ws_id != ws_id:
                    cursor.execute(f"""
                        SELECT transaction_type, amount 
                        FROM transactions 
                        WHERE (workspace_id = ? OR workspace_id = ?) AND telegram_user_id = ? AND deleted_at IS NULL
                    """, (ws_id, p_ws_id, int(user_id)))
                else:
                    cursor.execute(f"""
                        SELECT transaction_type, amount 
                        FROM transactions 
                        WHERE {ws_filter} AND telegram_user_id = ? AND deleted_at IS NULL
                    """, (ws_id, int(user_id)))
                all_u_rows = cursor.fetchall()
                u_sent = Decimal('0.00')
                u_recv = Decimal('0.00')
                for r in all_u_rows:
                    a = parse_decimal_amount(r['amount'], allow_zero=False)
                    if r['transaction_type'] == 'SENT':
                        u_sent += a
                    elif r['transaction_type'] == 'RECEIVED':
                        u_recv += a

                init_val = Decimal('0.00')
                if p_ws_id:
                    cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'initial_balance'", (p_ws_id,))
                    init_row = cursor.fetchone()
                    if init_row and init_row['value'] is not None:
                        try:
                            init_val = Decimal(str(init_row['value']))
                        except Exception:
                            init_val = Decimal('0.00')

                cur_bal = float(init_val + u_recv - u_sent)
        else:
            cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'current_balance'", (ws_id,))
            bal_row = cursor.fetchone()
            if not bal_row:
                cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                bal_row = cursor.fetchone()
            cur_bal = float(bal_row['value']) if bal_row and bal_row['value'] is not None else 0.0

        summary = TransactionSummary()
        summary.current_balance = cur_bal
        summary.transaction_count = len(rows)

        total_sent = Decimal('0.00')
        total_received = Decimal('0.00')

        for row in rows:
            amt = parse_decimal_amount(row['amount'], allow_zero=False)
            if row['transaction_type'] == 'SENT':
                total_sent += amt
            elif row['transaction_type'] == 'RECEIVED':
                total_received += amt

        summary.total_sent = float(total_sent)
        summary.total_received = float(total_received)
        summary.net_change = float(total_received - total_sent)
        return summary

def get_overall_summary(workspace_id: str = None, user_id: int = None) -> TransactionSummary:
    """Calculates summary across live transactions with Decimal precision, workspace isolation, and optional user filtering."""
    from database.queries import get_default_workspace_id, _build_user_filter
    ws_id = workspace_id or get_default_workspace_id()

    default_ws = get_default_workspace_id()
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')" if ws_id == default_ws else "workspace_id = ?"

    conditions = [ws_filter, "deleted_at IS NULL"]
    params = [ws_id]

    if user_id is not None:
        from config import TELEGRAM_USER_ID
        owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else None
        if not (owner_id and int(user_id) == owner_id):
            with get_db_connection() as conn_check:
                c_check = conn_check.cursor()
                c_check.execute("SELECT id FROM workspaces WHERE chat_id = ? AND is_active = 1", (int(user_id),))
                p_r = c_check.fetchone()
                if p_r and p_r['id'] and p_r['id'] != ws_id:
                    ws_filter = "(workspace_id = ? OR workspace_id = ?)"
                    conditions = [ws_filter, "deleted_at IS NULL"]
                    params = [ws_id, p_r['id']]

    u_sql, u_params = _build_user_filter(user_id)
    if u_sql:
        conditions.append(u_sql)
        params.extend(u_params)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT transaction_type, amount 
            FROM transactions 
            WHERE {' AND '.join(conditions)}
        """, params)
        rows = cursor.fetchall()

        if user_id is not None:
            from config import TELEGRAM_USER_ID
            owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else None
            if owner_id and int(user_id) == owner_id:
                cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'current_balance'", (ws_id,))
                bal_row = cursor.fetchone()
                if not bal_row:
                    cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                    bal_row = cursor.fetchone()
                cur_bal = float(bal_row['value']) if bal_row and bal_row['value'] is not None else 0.0
            else:
                cursor.execute("SELECT id FROM workspaces WHERE chat_id = ? AND is_active = 1", (int(user_id),))
                p_ws_row = cursor.fetchone()
                p_ws_id = p_ws_row['id'] if p_ws_row else None

                if p_ws_id and p_ws_id != ws_id:
                    cursor.execute(f"""
                        SELECT transaction_type, amount 
                        FROM transactions 
                        WHERE (workspace_id = ? OR workspace_id = ?) AND telegram_user_id = ? AND deleted_at IS NULL
                    """, (ws_id, p_ws_id, int(user_id)))
                else:
                    cursor.execute(f"""
                        SELECT transaction_type, amount 
                        FROM transactions 
                        WHERE {ws_filter} AND telegram_user_id = ? AND deleted_at IS NULL
                    """, (ws_id, int(user_id)))
                all_u_rows = cursor.fetchall()
                u_sent = Decimal('0.00')
                u_recv = Decimal('0.00')
                for r in all_u_rows:
                    a = parse_decimal_amount(r['amount'], allow_zero=False)
                    if r['transaction_type'] == 'SENT':
                        u_sent += a
                    elif r['transaction_type'] == 'RECEIVED':
                        u_recv += a

                init_val = Decimal('0.00')
                if p_ws_id:
                    cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'initial_balance'", (p_ws_id,))
                    init_row = cursor.fetchone()
                    if init_row and init_row['value'] is not None:
                        try:
                            init_val = Decimal(str(init_row['value']))
                        except Exception:
                            init_val = Decimal('0.00')

                cur_bal = float(init_val + u_recv - u_sent)
        else:
            cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'current_balance'", (ws_id,))
            bal_row = cursor.fetchone()
            if not bal_row:
                cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                bal_row = cursor.fetchone()
            cur_bal = float(bal_row['value']) if bal_row and bal_row['value'] is not None else 0.0

        summary = TransactionSummary()
        summary.current_balance = cur_bal
        summary.transaction_count = len(rows)

        total_sent = Decimal('0.00')
        total_received = Decimal('0.00')

        for row in rows:
            amt = parse_decimal_amount(row['amount'], allow_zero=False)
            if row['transaction_type'] == 'SENT':
                total_sent += amt
            elif row['transaction_type'] == 'RECEIVED':
                total_received += amt

        summary.total_sent = float(total_sent)
        summary.total_received = float(total_received)
        summary.net_change = float(total_received - total_sent)
        return summary

def resequence_transaction_ids() -> None:
    """
    DISCONTINUED: Stable permanent IDs are now maintained.
    Gaps in sequence are accepted and preserved to avoid shifting IDs across backups.
    """
    logger.debug("resequence_transaction_ids called — skipped to preserve stable permanent transaction IDs.")
    return

def validate_ledger_invariants(db_path=None, workspace_id: str = None, conn=None) -> List[str]:
    """
    Validates all ledger integrity invariants against the SQLite database.
    Multi-tenant aware: checks reference uniqueness and continuity per workspace.
    Checks:
      - broken chain (balance_before / balance_after continuity per workspace)
      - wrong signs (SENT increases balance, RECEIVED decreases, negative amounts)
      - invalid transaction types (must be SENT, RECEIVED, or TRANSFER)
      - non-positive amounts (amount <= 0, NaN, Inf)
      - current_balance mismatch in workspace_settings / settings
      - duplicate live reference numbers within a workspace
      - invalid or missing UIDs
      - inconsistent deleted_at values
    Never auto-repairs. Returns a list of human-readable error strings.
    """
    errors: List[str] = []

    cm = get_db_connection(db_path=db_path) if conn is None else None
    active_conn = conn if conn is not None else cm.__enter__()
    try:
        cursor = active_conn.cursor()
        from database.queries import get_default_workspace_id
        default_ws = get_default_workspace_id()

        # 1. Inspect all rows (live and deleted) for structural validity
        if workspace_id:
            cursor.execute("SELECT * FROM transactions WHERE workspace_id = ? ORDER BY id ASC", (str(workspace_id),))
        else:
            cursor.execute("SELECT * FROM transactions ORDER BY id ASC")
        all_txs = cursor.fetchall()

        seen_live_refs = {}
        distinct_workspaces = set()

        for row in all_txs:
            row_id = row['id']
            tt = row['transaction_type']
            amt = row['amount']
            uid = row['uid']
            del_at = row['deleted_at']
            ref = row['reference_number']
            ws_id = row['workspace_id'] or default_ws
            distinct_workspaces.add(ws_id)

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

            # Duplicate live reference numbers scoped per workspace
            if del_at is None and ref and str(ref).strip():
                clean_ref = str(ref).strip()
                ref_key = (ws_id, clean_ref)
                if ref_key in seen_live_refs:
                    errors.append(f"Duplicate live reference_number {clean_ref!r} on rows {seen_live_refs[ref_key]} and {row_id}")
                else:
                    seen_live_refs[ref_key] = row_id

            # Inconsistent deleted_at
            if del_at is not None:
                del_str = str(del_at).strip()
                if not del_str or del_str.lower() in ('none', 'null', '0', 'false'):
                    errors.append(f"Row {row_id}: inconsistent deleted_at value {del_at!r}")

        # 2. Determine workspaces to validate for continuity
        if workspace_id:
            target_workspaces = [workspace_id]
        elif distinct_workspaces:
            target_workspaces = sorted(distinct_workspaces)
        else:
            target_workspaces = [default_ws]

        # 3. Check ledger continuity on live transactions per workspace
        for ws in target_workspaces:
            # Fetch initial_balance for this workspace
            cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'initial_balance'", (ws,))
            ws_init_row = cursor.fetchone()
            if not ws_init_row and ws == default_ws:
                cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
                ws_init_row = cursor.fetchone()

            if ws_init_row:
                try:
                    raw_init = Decimal(str(ws_init_row['value']))
                    if not raw_init.is_finite():
                        errors.append(f"Workspace {ws}: Invalid initial_balance setting: non-finite Decimal")
                        ws_initial_bal = Decimal('0.00')
                    else:
                        ws_initial_bal = raw_init.quantize(CENT)
                except Exception as err:
                    errors.append(f"Workspace {ws}: Invalid initial_balance setting: {err}")
                    ws_initial_bal = Decimal('0.00')
            else:
                ws_initial_bal = None

            # Fetch current_balance for this workspace
            cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'current_balance'", (ws,))
            ws_cur_row = cursor.fetchone()
            if not ws_cur_row and ws == default_ws:
                cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
                ws_cur_row = cursor.fetchone()

            try:
                raw_cur = Decimal(str(ws_cur_row['value'])) if ws_cur_row else None
                if raw_cur is not None and not raw_cur.is_finite():
                    errors.append(f"Workspace {ws}: Invalid current_balance setting: non-finite Decimal")
                    ws_current_bal = None
                else:
                    ws_current_bal = raw_cur.quantize(CENT) if raw_cur is not None else None
            except Exception as err:
                errors.append(f"Workspace {ws}: Invalid current_balance setting: {err}")
                ws_current_bal = None

            if ws == default_ws:
                cursor.execute('''
                    SELECT id, transaction_type, amount, balance_before, balance_after, occurred_at, created_at
                    FROM transactions
                    WHERE (workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')
                      AND deleted_at IS NULL
                    ORDER BY occurred_at ASC, created_at ASC, id ASC
                ''', (ws,))
            else:
                cursor.execute('''
                    SELECT id, transaction_type, amount, balance_before, balance_after, occurred_at, created_at
                    FROM transactions
                    WHERE workspace_id = ?
                      AND deleted_at IS NULL
                    ORDER BY occurred_at ASC, created_at ASC, id ASC
                ''', (ws,))
            live_txs = cursor.fetchall()

            if ws_initial_bal is not None:
                expected_balance = ws_initial_bal
            elif live_txs:
                expected_balance = Decimal(str(live_txs[0]['balance_before'])).quantize(CENT)
            else:
                expected_balance = Decimal('0.00')
            for row in live_txs:
                row_id = row['id']
                tt = row['transaction_type']

                try:
                    raw_amt = Decimal(str(row['amount']))
                    if not raw_amt.is_finite() or raw_amt <= 0:
                        dec_amt = None
                        errors.append(f"Row {row_id}: non-positive or non-finite amount in ledger continuity: {row['amount']}")
                    else:
                        dec_amt = raw_amt.quantize(CENT)
                except Exception as e:
                    dec_amt = None
                    errors.append(f"Row {row_id}: malformed amount in ledger continuity: {e}")

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

                # Broken chain
                if bal_before != expected_balance:
                    errors.append(f"Row {row_id}: broken chain balance_before mismatch (expected {expected_balance}, got {bal_before})")

                # Validate signs and balance_after
                if dec_amt is not None and bal_before is not None:
                    if tt == 'SENT':
                        calc_after = bal_before - dec_amt
                    elif tt == 'RECEIVED':
                        calc_after = bal_before + dec_amt
                    elif tt == 'TRANSFER':
                        calc_after = bal_before
                    else:
                        calc_after = None

                    if calc_after is not None and bal_after != calc_after:
                        errors.append(f"Row {row_id}: broken chain balance_after mismatch (expected {calc_after}, got {bal_after})")

                    if calc_after is not None:
                        expected_balance = calc_after
                    elif bal_after is not None:
                        expected_balance = bal_after
                else:
                    if bal_after is not None:
                        expected_balance = bal_after

            # Check that current_balance matches the end of the chain
            if ws_current_bal is not None and ws_current_bal != expected_balance:
                errors.append(f"Workspace {ws}: current_balance mismatch (expected {expected_balance}, found {ws_current_bal})")
    finally:
        if cm is not None:
            cm.__exit__(None, None, None)

    return errors

