"""
Undo Service for Payment Tracker.
Stores reversible snapshots of recent delete, edit, and insert operations in memory/state
so users can easily revert actions with /undo or an interactive '↩️ Undo' button.
"""
from typing import Optional, Dict, Any
from config import logger

# In-memory stack of recent undoable actions (holds up to last 20 actions)
_UNDO_STACK = []

def record_delete_action(deleted_tx: dict):
    """Records a deleted transaction snapshot so it can be restored."""
    if not deleted_tx:
        return
    _UNDO_STACK.append({
        'action_type': 'delete',
        'data': dict(deleted_tx)
    })
    if len(_UNDO_STACK) > 20:
        _UNDO_STACK.pop(0)
    logger.info(f"Recorded undo action for deleted transaction #{deleted_tx.get('id')}")

def record_edit_action(previous_tx: dict):
    """Records the prior state of a transaction before edits were applied."""
    if not previous_tx:
        return
    _UNDO_STACK.append({
        'action_type': 'edit',
        'data': dict(previous_tx)
    })
    if len(_UNDO_STACK) > 20:
        _UNDO_STACK.pop(0)
    logger.info(f"Recorded undo action for edited transaction #{previous_tx.get('id')}")

def record_insert_action(inserted_tx_id: int):
    """Records a freshly inserted transaction ID so it can be undone (deleted)."""
    _UNDO_STACK.append({
        'action_type': 'insert',
        'tx_id': inserted_tx_id
    })
    if len(_UNDO_STACK) > 20:
        _UNDO_STACK.pop(0)
    logger.info(f"Recorded undo action for new transaction #{inserted_tx_id}")

def get_last_action() -> Optional[Dict[str, Any]]:
    """Returns the most recent undoable action without removing it."""
    return _UNDO_STACK[-1] if _UNDO_STACK else None

def pop_last_action() -> Optional[Dict[str, Any]]:
    """Pops and returns the most recent undoable action."""
    return _UNDO_STACK.pop() if _UNDO_STACK else None

def perform_undo() -> tuple[bool, str]:
    """
    Reverts the last action performed (delete, edit, or insert).
    Returns (success: bool, message: str).
    """
    if not _UNDO_STACK:
        return False, "No recent action found to undo."

    action = _UNDO_STACK.pop()
    action_type = action.get('action_type')

    try:
        from database.db import get_db_connection
        from services.balance_service import resequence_transaction_ids, recalculate_all_balances
        from database.queries import update_transaction, delete_transaction, get_balance_setting
        from utils.currency import format_currency
        from services.backup_service import export_database_to_json
        from database.queries import restore_soft_deleted_transaction
        import html

        if action_type == 'delete':
            # Restore the soft-deleted transaction
            tx = action['data']
            tx_id = tx.get('id')
            tx_uid = tx.get('uid')
            
            restored = restore_soft_deleted_transaction(tx_id=tx_id, uid=tx_uid)
            if not restored:
                # Fallback: if row was somehow physically deleted, re-insert with its original uid
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO transactions (
                            id, transaction_type, amount, person_name, sender_name, recipient_name,
                            upi_id, phone_number, transaction_date, transaction_time, reference_number,
                            transaction_id, payment_app, bank_name, bank_account, payment_status,
                            category, balance_before, balance_after, ocr_text, original_image_path,
                            telegram_message_id, telegram_chat_id, uid, deleted_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    ''', (
                        tx_id,
                        tx.get('transaction_type', 'RECEIVED'),
                        float(tx.get('amount', 0.0)),
                        tx.get('person_name', ''),
                        tx.get('sender_name', ''),
                        tx.get('recipient_name', ''),
                        tx.get('upi_id', ''),
                        tx.get('phone_number', ''),
                        tx.get('transaction_date'),
                        tx.get('transaction_time', ''),
                        tx.get('reference_number', ''),
                        tx.get('transaction_id', ''),
                        tx.get('payment_app', ''),
                        tx.get('bank_name', ''),
                        tx.get('bank_account', ''),
                        tx.get('payment_status', 'SUCCESS'),
                        tx.get('category', 'General'),
                        float(tx.get('balance_before', 0.0)),
                        float(tx.get('balance_after', 0.0)),
                        tx.get('ocr_text', ''),
                        tx.get('original_image_path', ''),
                        str(tx.get('telegram_message_id', '')),
                        str(tx.get('telegram_chat_id', '')),
                        tx_uid,
                        tx.get('created_at'),
                        tx.get('updated_at')
                    ))
                    conn.commit()

            new_bal = recalculate_all_balances()
            export_database_to_json()

            amt_s = format_currency(tx.get('amount', 0))
            person_s = tx.get('person_name') or 'Unknown'
            return True, (
                f"↩️ <b>Undo Successful! Restored Deleted Transaction #{tx_id}</b>\n\n"
                f"• <b>Type:</b> {html.escape(str(tx.get('transaction_type')))}\n"
                f"• <b>Person:</b> {html.escape(str(person_s))}\n"
                f"• <b>Amount:</b> <b>{html.escape(amt_s)}</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        elif action_type == 'edit':
            # Restore the previous transaction state
            old_tx = action['data']
            tx_id = old_tx['id']
            updates = {
                'transaction_type': old_tx.get('transaction_type'),
                'amount': float(old_tx.get('amount', 0.0)),
                'person_name': old_tx.get('person_name', ''),
                'sender_name': old_tx.get('sender_name', ''),
                'recipient_name': old_tx.get('recipient_name', ''),
                'upi_id': old_tx.get('upi_id', ''),
                'transaction_date': old_tx.get('transaction_date'),
                'reference_number': old_tx.get('reference_number', ''),
                'category': old_tx.get('category', 'General')
            }
            update_transaction(tx_id, updates)
            new_bal = recalculate_all_balances()
            export_database_to_json()

            amt_s = format_currency(old_tx.get('amount', 0))
            return True, (
                f"↩️ <b>Undo Successful! Reverted Edit on Transaction #{tx_id}</b>\n\n"
                f"• <b>Person:</b> {html.escape(str(old_tx.get('person_name') or 'Unknown'))}\n"
                f"• <b>Amount:</b> <b>{html.escape(amt_s)}</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        elif action_type == 'insert':
            tx_id = action['tx_id']
            delete_transaction(tx_id)
            new_bal = recalculate_all_balances()
            export_database_to_json()
            return True, (
                f"↩️ <b>Undo Successful! Removed newly added transaction #{tx_id}.</b>\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>"
            )

        return False, "Unknown action type to undo."

    except Exception as e:
        logger.error(f"Error performing undo: {e}", exc_info=True)
        return False, f"Failed to undo: {e}"
