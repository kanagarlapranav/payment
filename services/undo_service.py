"""
Undo Service for Payment Tracker.
Stores reversible undo records in the SQLite table `undo_log` scoped by (chat_id, user_id),
expiring after 10 minutes, and enforcing exactly-once consumption.
Undo of a delete restores by permanent UID only without ever recreating purged rows from snapshots.
"""
import html
import json
from datetime import datetime, timezone
from typing import Any

from config import TELEGRAM_GROUP_ID, TELEGRAM_USER_ID, logger
from database.db import LEDGER_LOCK, get_db_connection
from database.queries import (
    delete_transaction_by_uid,
    get_balance_setting,
    get_default_workspace_id,
    get_transaction_by_id,
    get_transaction_by_uid,
    restore_soft_deleted_transaction,
)
from utils.currency import format_currency
from utils.dates import utc_now_iso
from utils.validation import validate_uid

# Backwards-compatibility alias for legacy imports
_UNDO_STACK = []


def _resolve_scope(chat_id: int | None, user_id: int | None, workspace_id: str | None = None) -> tuple[int, int, str]:
    """Resolves effective chat_id, user_id, and workspace_id."""
    c_id = int(chat_id) if chat_id is not None else int(TELEGRAM_GROUP_ID or TELEGRAM_USER_ID or 0)
    u_id = int(user_id) if user_id is not None else int(TELEGRAM_USER_ID or 0)
    if workspace_id and str(workspace_id).strip():
        ws_id = str(workspace_id).strip()
    else:
        from database.queries import get_default_workspace_id
        ws_id = get_default_workspace_id()
    return c_id, u_id, ws_id


def record_delete_action(
    deleted_tx: dict,
    chat_id: int | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
) -> bool:
    """
    Records a soft-deleted transaction in undo_log by its permanent UID.
    Scoped by (workspace_id, chat_id, user_id).
    """
    if not deleted_tx:
        return False

    ws_id = workspace_id or deleted_tx.get('workspace_id') or get_default_workspace_id()
    uid = deleted_tx.get('uid')
    if not uid and deleted_tx.get('id'):
        row = get_transaction_by_id(deleted_tx['id'], workspace_id=ws_id)
        if row:
            uid = row.get('uid')

    if not uid:
        logger.warning(f"Cannot record undo for transaction without uid: {deleted_tx}")
        return False

    valid_uid = validate_uid(uid)
    c_id, u_id, resolved_ws_id = _resolve_scope(chat_id, user_id, ws_id)
    now_utc = utc_now_iso()

    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO undo_log (workspace_id, chat_id, user_id, action, uid, created_at)
            VALUES (?, ?, ?, 'delete', ?, ?)
            """,
            (resolved_ws_id or None, c_id, u_id, valid_uid, now_utc),
        )
        conn.commit()

    logger.info(f"Recorded undo delete for UID {valid_uid} scoped to workspace={resolved_ws_id}, chat={c_id}, user={u_id}")
    return True


def record_insert_action(
    inserted_tx_uid_or_id: Any,
    chat_id: int | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
) -> bool:
    """
    Records a freshly inserted transaction in undo_log by its permanent UID so it can be undone.
    Scoped by (workspace_id, chat_id, user_id).
    """
    uid = None
    tx_ws_id = workspace_id
    if not tx_ws_id:
        with get_db_connection() as conn_lookup:
            cur = conn_lookup.cursor()
            if isinstance(inserted_tx_uid_or_id, int) or (isinstance(inserted_tx_uid_or_id, str) and str(inserted_tx_uid_or_id).isdigit()):
                cur.execute("SELECT uid, workspace_id FROM transactions WHERE id = ?", (int(inserted_tx_uid_or_id),))
            else:
                cur.execute("SELECT uid, workspace_id FROM transactions WHERE uid = ?", (str(inserted_tx_uid_or_id),))
            t_row = cur.fetchone()
            if t_row:
                uid = t_row['uid']
                tx_ws_id = t_row['workspace_id']

    if not uid and tx_ws_id:
        if isinstance(inserted_tx_uid_or_id, int) or (isinstance(inserted_tx_uid_or_id, str) and str(inserted_tx_uid_or_id).isdigit()):
            row = get_transaction_by_id(int(inserted_tx_uid_or_id), workspace_id=tx_ws_id)
            if row:
                uid = row.get('uid')
        elif inserted_tx_uid_or_id:
            uid = str(inserted_tx_uid_or_id).strip()

    if not uid:
        return False

    valid_uid = validate_uid(uid)
    tx_ws_id = tx_ws_id or get_default_workspace_id()

    c_id, u_id, resolved_ws_id = _resolve_scope(chat_id, user_id, tx_ws_id)
    now_utc = utc_now_iso()

    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO undo_log (workspace_id, chat_id, user_id, action, uid, created_at)
            VALUES (?, ?, ?, 'insert', ?, ?)
            """,
            (resolved_ws_id or None, c_id, u_id, valid_uid, now_utc),
        )
        conn.commit()

    logger.info(f"Recorded undo insert for UID {valid_uid} scoped to workspace={resolved_ws_id}, chat={c_id}, user={u_id}")
    return True


def record_edit_action(
    previous_tx: dict,
    chat_id: int | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
) -> bool:
    """
    Records an edit action in undo_log, saving previous transaction state in snapshot_json.
    Scoped by (workspace_id, chat_id, user_id).
    """
    if not previous_tx:
        return False

    ws_id = workspace_id or previous_tx.get('workspace_id') or get_default_workspace_id()
    uid = previous_tx.get('uid')
    if not uid and previous_tx.get('id'):
        row = get_transaction_by_id(previous_tx['id'], workspace_id=ws_id)
        if row:
            uid = row.get('uid')

    if not uid:
        logger.warning(f"Cannot record undo edit for transaction without uid: {previous_tx}")
        return False

    valid_uid = validate_uid(uid)
    c_id, u_id, resolved_ws_id = _resolve_scope(chat_id, user_id, ws_id)
    now_utc = utc_now_iso()

    snapshot = {
        'amount': previous_tx.get('amount'),
        'person_name': previous_tx.get('person_name'),
        'transaction_type': previous_tx.get('transaction_type'),
        'transaction_date': previous_tx.get('transaction_date'),
        'reference_number': previous_tx.get('reference_number'),
        'category': previous_tx.get('category'),
        'sender_name': previous_tx.get('sender_name'),
        'recipient_name': previous_tx.get('recipient_name')
    }
    snapshot_json = json.dumps(snapshot)

    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO undo_log (workspace_id, chat_id, user_id, action, uid, snapshot_json, created_at)
            VALUES (?, ?, ?, 'edit', ?, ?, ?)
            """,
            (resolved_ws_id or None, c_id, u_id, valid_uid, snapshot_json, now_utc),
        )
        conn.commit()

    logger.info(f"Recorded undo edit for UID {valid_uid} scoped to workspace={resolved_ws_id}, chat={c_id}, user={u_id}")
    return True


def get_last_action(
    chat_id: int | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    """Returns the most recent active (unused, unexpired) undo action without consuming it."""
    c_id, u_id, ws_id = _resolve_scope(chat_id, user_id, workspace_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if ws_id:
            from database.queries import get_default_workspace_id
            default_ws = get_default_workspace_id()
            ws_clause = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
            cursor.execute(
                f"""
                SELECT id, workspace_id, chat_id, user_id, action, uid, snapshot_json, created_at
                FROM undo_log
                WHERE {ws_clause} AND user_id = ? AND used_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (ws_id, u_id),
            )
        else:
            cursor.execute(
                """
                SELECT id, workspace_id, chat_id, user_id, action, uid, snapshot_json, created_at
                FROM undo_log
                WHERE chat_id = ? AND user_id = ? AND used_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (c_id, u_id),
            )
        row = cursor.fetchone()
        return dict(row) if row else None


def perform_undo(
    chat_id: int | None = None,
    user_id: int | None = None,
    workspace_id: str | None = None,
) -> tuple[bool, str]:
    """
    Reverts the last action performed for (workspace_id, chat_id, user_id).
    Enforces tenant workspace boundaries, 10-minute expiry, and exactly-once consumption.
    """
    c_id, u_id, ws_id = _resolve_scope(chat_id, user_id, workspace_id)

    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if ws_id:
                from database.queries import get_default_workspace_id
                default_ws = get_default_workspace_id()
                ws_clause = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
                cursor.execute(
                    f"""
                    SELECT id, workspace_id, chat_id, user_id, action, uid, snapshot_json, created_at
                    FROM undo_log
                    WHERE {ws_clause} AND user_id = ? AND used_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (ws_id, u_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, workspace_id, chat_id, user_id, action, uid, snapshot_json, created_at
                    FROM undo_log
                    WHERE chat_id = ? AND user_id = ? AND used_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (c_id, u_id),
                )
            row = cursor.fetchone()
            if not row:
                return False, "No recent action found to undo."

            rec_id = row['id']
            action = row['action']
            uid = row['uid']
            rec_ws_id = row['workspace_id'] or ws_id
            if not rec_ws_id:
                with get_db_connection() as c_check:
                    cur_c = c_check.cursor()
                    cur_c.execute("SELECT workspace_id FROM transactions WHERE uid = ?", (uid,))
                    t_row = cur_c.fetchone()
                    if t_row and t_row['workspace_id']:
                        rec_ws_id = t_row['workspace_id']
                    else:
                        from database.queries import get_default_workspace_id
                        rec_ws_id = get_default_workspace_id()

            created_at_str = row['created_at']

            # Check 10-minute expiration window
            try:
                created_dt = datetime.fromisoformat(created_at_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - created_dt).total_seconds()
            except (ValueError, TypeError):
                # Fail closed on unparseable timestamps (P2-bq)
                age_seconds = 999999

            now_iso = utc_now_iso()

            if age_seconds > 600:
                cursor.execute("UPDATE undo_log SET used_at = ? WHERE id = ?", (now_iso, rec_id))
                conn.commit()
                return False, "Undo action has expired (window is 10 minutes)."

        # Execute undo action with strict workspace scoping
        if action == 'delete':
            tx = get_transaction_by_uid(uid, workspace_id=rec_ws_id)
            if not tx:
                return False, "Recovery is not possible: transaction record does not belong to this workspace or no longer exists."

            # Verify workspace match
            if ws_id and tx.get('workspace_id') and str(tx['workspace_id']) != str(ws_id):
                return False, "Cross-workspace undo rejected: transaction does not belong to current workspace."

            if tx.get('deleted_at') is None:
                return False, "Cannot restore transaction: transaction is already active."

            restored = restore_soft_deleted_transaction(uid=uid)
            if not restored:
                return False, "Recovery is not possible: failed to restore transaction."

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE undo_log SET used_at = ? WHERE id = ? AND used_at IS NULL",
                    (now_iso, rec_id),
                )
                conn.commit()

            from database.queries import get_workspace_member
            from config import SUPER_ADMIN_IDS
            umem = get_workspace_member(str(rec_ws_id), int(u_id)) if u_id else None
            u_role = umem.role if umem else ("owner" if int(u_id or 0) in SUPER_ADMIN_IDS else "member")
            from services.audit_service import log_audit_event
            log_audit_event(
                workspace_id=rec_ws_id,
                actor_user_id=u_id,
                actor_role=u_role,
                action="transaction_restored_undo",
                resource=f"tx:{uid}"
            )

            new_bal = get_balance_setting(workspace_id=rec_ws_id)
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except (OSError, RuntimeError) as bkp_err:
                logger.debug(f"Undo backup notice: {bkp_err}")

            tx_id = tx.get('id')
            amt_s = format_currency(tx.get('amount', 0))
            person_s = tx.get('person_name') or 'Unknown'
            return True, (
                f"↩️ <b>Undo Successful! Restored Deleted Transaction #{tx_id}</b>\n\n"
                f"• <b>Type:</b> {html.escape(str(tx.get('transaction_type')))}\n"
                f"• <b>Person:</b> {html.escape(str(person_s))}\n"
                f"• <b>Amount:</b> <b>{html.escape(amt_s)}</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        elif action == 'insert':
            tx = get_transaction_by_uid(uid, live_only=True, workspace_id=rec_ws_id)
            if not tx:
                return False, "Transaction is already deleted or no longer exists."

            # Verify workspace match
            if ws_id and tx.get('workspace_id') and str(tx['workspace_id']) != str(ws_id):
                return False, "Cross-workspace undo rejected: transaction does not belong to current workspace."

            tx_id = tx.get('id')
            deleted = delete_transaction_by_uid(uid=uid, workspace_id=rec_ws_id)
            if not deleted:
                return False, "Failed to remove newly added transaction."

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE undo_log SET used_at = ? WHERE id = ? AND used_at IS NULL",
                    (now_iso, rec_id),
                )
                conn.commit()

            new_bal = get_balance_setting(workspace_id=rec_ws_id)
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except (OSError, RuntimeError) as bkp_err:
                logger.debug(f"Undo insert backup notice: {bkp_err}")

            return True, (
                f"↩️ <b>Undo Successful! Removed newly added transaction #{tx_id}.</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        elif action == 'edit':
            tx = get_transaction_by_uid(uid, workspace_id=rec_ws_id)
            if not tx:
                return False, "Transaction record no longer exists or does not belong to this workspace."

            if ws_id and tx.get('workspace_id') and str(tx['workspace_id']) != str(ws_id):
                return False, "Cross-workspace undo rejected: transaction does not belong to current workspace."

            snapshot_raw = row['snapshot_json']
            if not snapshot_raw:
                return False, "No previous snapshot data found to restore."

            try:
                snapshot = json.loads(snapshot_raw)
            except Exception as e:
                return False, f"Corrupted undo snapshot: {e}"

            from database.queries import update_transaction, recalculate_all_balances
            updated = update_transaction(tx['id'], snapshot, workspace_id=rec_ws_id)
            if not updated:
                return False, "Failed to restore previous transaction values."

            recalculate_all_balances(workspace_id=rec_ws_id)
            new_bal = get_balance_setting(workspace_id=rec_ws_id)

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE undo_log SET used_at = ? WHERE id = ? AND used_at IS NULL",
                    (now_iso, rec_id),
                )
                conn.commit()

            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except (OSError, RuntimeError) as bkp_err:
                logger.debug(f"Undo backup notice: {bkp_err}")

            tx_id = tx.get('id')
            amt_s = format_currency(snapshot.get('amount', tx.get('amount', 0)))
            person_s = snapshot.get('person_name', tx.get('person_name', 'Unknown'))
            return True, (
                f"↩️ <b>Undo Successful! Reverted Edit on Transaction #{tx_id}</b>\n\n"
                f"• <b>Person:</b> {html.escape(str(person_s))}\n"
                f"• <b>Amount:</b> <b>{html.escape(amt_s)}</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        return False, f"Unknown action type '{action}'."
