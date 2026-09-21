"""
Undo Service for Payment Tracker.
Stores reversible undo records in the SQLite table `undo_log` scoped by (chat_id, user_id),
expiring after 10 minutes, and enforcing exactly-once consumption.
Undo of a delete restores by permanent UID only without ever recreating purged rows from snapshots.
"""
import html
from datetime import datetime, timezone
from typing import Any

from config import TELEGRAM_GROUP_ID, TELEGRAM_USER_ID, logger
from database.db import LEDGER_LOCK, get_db_connection
from database.queries import (
    delete_transaction_by_uid,
    get_balance_setting,
    get_transaction_by_id,
    get_transaction_by_uid,
    restore_soft_deleted_transaction,
)
from utils.currency import format_currency
from utils.dates import utc_now_iso
from utils.validation import validate_uid

# Backwards-compatibility alias for legacy imports
_UNDO_STACK = []


def _resolve_scope(chat_id: int | None, user_id: int | None) -> tuple[int, int]:
    """Resolves effective chat_id and user_id with fallback to configured defaults."""
    c_id = int(chat_id) if chat_id is not None else int(TELEGRAM_GROUP_ID or TELEGRAM_USER_ID or 0)
    u_id = int(user_id) if user_id is not None else int(TELEGRAM_USER_ID or 0)
    return c_id, u_id


def record_delete_action(
    deleted_tx: dict,
    chat_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """
    Records a soft-deleted transaction in undo_log by its permanent UID.
    Scoped by (chat_id, user_id).
    """
    if not deleted_tx:
        return False

    uid = deleted_tx.get('uid')
    if not uid and deleted_tx.get('id'):
        row = get_transaction_by_id(deleted_tx['id'])
        if row:
            uid = row.get('uid')

    if not uid:
        logger.warning(f"Cannot record undo for transaction without uid: {deleted_tx}")
        return False

    valid_uid = validate_uid(uid)
    c_id, u_id = _resolve_scope(chat_id, user_id)
    now_utc = utc_now_iso()

    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
                INSERT INTO undo_log (chat_id, user_id, action, uid, created_at)
                VALUES (?, ?, 'delete', ?, ?)
                """,
            (c_id, u_id, valid_uid, now_utc),
        )
        conn.commit()

    logger.info(f"Recorded undo delete for UID {valid_uid} scoped to chat {c_id}, user {u_id}")
    return True


def record_insert_action(
    inserted_tx_uid_or_id: Any,
    chat_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """
    Records a freshly inserted transaction in undo_log by its permanent UID so it can be undone.
    Scoped by (chat_id, user_id).
    """
    uid = None
    if isinstance(inserted_tx_uid_or_id, int) or (isinstance(inserted_tx_uid_or_id, str) and inserted_tx_uid_or_id.isdigit()):
        row = get_transaction_by_id(int(inserted_tx_uid_or_id))
        if row:
            uid = row.get('uid')
    elif inserted_tx_uid_or_id:
        uid = str(inserted_tx_uid_or_id).strip()

    if not uid:
        return False

    valid_uid = validate_uid(uid)
    c_id, u_id = _resolve_scope(chat_id, user_id)
    now_utc = utc_now_iso()

    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
                INSERT INTO undo_log (chat_id, user_id, action, uid, created_at)
                VALUES (?, ?, 'insert', ?, ?)
                """,
            (c_id, u_id, valid_uid, now_utc),
        )
        conn.commit()

    logger.info(f"Recorded undo insert for UID {valid_uid} scoped to chat {c_id}, user {u_id}")
    return True


def record_edit_action(
    previous_tx: dict,
    chat_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """Backwards-compatibility stub for recording edit operations."""
    return record_delete_action(previous_tx, chat_id=chat_id, user_id=user_id)


def get_last_action(chat_id: int | None = None, user_id: int | None = None) -> dict[str, Any] | None:
    """Returns the most recent active (unused, unexpired) undo action without consuming it."""
    c_id, u_id = _resolve_scope(chat_id, user_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, chat_id, user_id, action, uid, created_at
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
) -> tuple[bool, str]:
    """
    Reverts the last action performed for (chat_id, user_id).
    Enforces:
      - Scope by (chat_id, user_id)
      - Expiration after 10 minutes
      - Exactly-once usage
      - Restores deletes by permanent UID only
      - Never recreates purged rows from snapshot
    Returns (success: bool, message: str).
    """
    c_id, u_id = _resolve_scope(chat_id, user_id)

    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Fetch latest unused undo record for this scope
            cursor.execute(
                """
                SELECT id, chat_id, user_id, action, uid, created_at
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
            created_at_str = row['created_at']

            # Check 10-minute expiration window
            try:
                created_dt = datetime.fromisoformat(created_at_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - created_dt).total_seconds()
            except (ValueError, TypeError):
                age_seconds = 0

            now_iso = utc_now_iso()

            if age_seconds > 600:
                # Expired: mark as consumed/expired
                cursor.execute("UPDATE undo_log SET used_at = ? WHERE id = ?", (now_iso, rec_id))
                conn.commit()
                return False, "Undo action has expired (window is 10 minutes)."

            # Consume record exactly once
            cursor.execute(
                "UPDATE undo_log SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (now_iso, rec_id),
            )
            if cursor.rowcount != 1:
                return False, "Undo action has already been used."
            conn.commit()

        # Execute undo action outside cursor transaction to allow sub-functions their own connection
        if action == 'delete':
            # Check if tombstone exists in transactions
            tx = get_transaction_by_uid(uid)
            if not tx:
                return False, "Recovery is not possible: transaction record no longer exists."

            if tx.get('deleted_at') is None:
                return False, "Cannot restore transaction: transaction is already active."

            restored = restore_soft_deleted_transaction(uid=uid)
            if not restored:
                return False, "Recovery is not possible: failed to restore transaction."

            new_bal = get_balance_setting()
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
            tx = get_transaction_by_uid(uid, live_only=True)
            if not tx:
                return False, "Transaction is already deleted or no longer exists."

            tx_id = tx.get('id')
            delete_transaction_by_uid(uid=uid)
            new_bal = get_balance_setting()
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except (OSError, RuntimeError) as bkp_err:
                logger.debug(f"Undo insert backup notice: {bkp_err}")

            return True, (
                f"↩️ <b>Undo Successful! Removed newly added transaction #{tx_id}.</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        return False, f"Unknown action type '{action}'."
