import asyncio
from unittest.mock import MagicMock, AsyncMock
from database.queries import get_default_workspace_id, add_workspace_member
from bot.feature_registry import set_feature_permission
from bot.callbacks.nav import nav_budget, nav_history
from bot.callbacks.cafe import handle_cafe_callback


def test_nav_callbacks_enforce_feature_matrix():
    async def _run():
        ws_id = get_default_workspace_id()
        member_id = 888801
        add_workspace_member(ws_id, member_id, role="member")

        # Revoke budget for member
        set_feature_permission(ws_id, "budget", "member", False)

        query = MagicMock()
        query.from_user.id = member_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        ws_ctx = MagicMock(role="member")

        # 1. nav_budget
        await nav_budget(query, MagicMock(), ["nav", "budget"], ws_id, ws_ctx)
        query.answer.assert_called_with("⛔ This feature is disabled for your role.", show_alert=True)
        query.edit_message_text.assert_not_called()

        # 2. nav_history with history revoked
        set_feature_permission(ws_id, "history", "member", False)
        query.answer.reset_mock()
        query.edit_message_text.reset_mock()
        await nav_history(query, MagicMock(), ["nav", "history"], ws_id, ws_ctx)
        query.answer.assert_called_with("⛔ This feature is disabled for your role.", show_alert=True)
        query.edit_message_text.assert_not_called()

        # 3. cafe action with cafe revoked
        set_feature_permission(ws_id, "cafe", "member", False)
        query.answer.reset_mock()
        query.edit_message_text.reset_mock()
        handled = await handle_cafe_callback(query, MagicMock(), "cafe_pick", ["cafe_pick", "1", "Tea"], ws_id, ws_ctx)
        assert handled is True
        query.answer.assert_called_with("⛔ This feature is disabled for your role.", show_alert=True)
        query.edit_message_text.assert_not_called()

    asyncio.run(_run())


def test_set_explicit_balance_requires_workspace_id():
    import pytest
    from services.balance_service import set_explicit_balance
    from database.queries import get_default_workspace_id
    from database.db import get_db_connection

    ws_id = get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'initial_balance'", (ws_id,))
        row_before = cursor.fetchone()
        val_before = row_before['value'] if row_before else None

    with pytest.raises(ValueError, match="workspace_id is required"):
        set_explicit_balance(100, workspace_id=None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'initial_balance'", (ws_id,))
        row_after = cursor.fetchone()
        val_after = row_after['value'] if row_after else None

    assert val_after == val_before


def test_startup_does_not_adopt_orphaned_transactions():
    from unittest.mock import patch
    from database.db import get_db_connection, setup_database
    from database.models import Transaction
    from database.queries import insert_transaction, get_default_workspace_id

    default_ws = get_default_workspace_id()
    tx = Transaction(
        amount=50.0,
        transaction_type="SENT",
        person_name="Fix3 Orphan Test",
        transaction_date="2026-10-11",
        workspace_id=default_ws
    )
    tx_id = insert_transaction(tx)
    with get_db_connection() as conn:
        conn.cursor().execute("UPDATE transactions SET workspace_id = NULL WHERE id = ?", (tx_id,))
        conn.commit()

    with patch('database.db.logger.warning') as mock_warn:
        setup_database()
        warning_called = any("orphaned rows detected, left in place for manual repair" in str(c) for c in mock_warn.call_args_list)
        assert warning_called, "Expected warning about orphaned rows from startup init"

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT workspace_id FROM transactions WHERE id = ?", (tx_id,))
        row = cur.fetchone()
        assert row['workspace_id'] is None, "Expected orphaned row workspace_id to remain NULL"


def test_menu_main_callback_renders_home_menu():
    async def _run():
        from bot.handlers import _dispatch_callback_query
        from database.queries import get_default_workspace_id

        ws_id = get_default_workspace_id()
        update = MagicMock()
        update.callback_query.data = "menu_main"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        update.effective_user.id = 123456789
        update.effective_chat.id = 123456789
        update.callback_query.from_user.id = 123456789
        context = MagicMock()

        await _dispatch_callback_query(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Payment Tracker" in text or "Active Ledger" in text

    asyncio.run(_run())


def test_perm_remove_confirm_fails_closed_when_no_workspace():
    async def _run():
        from bot.handlers import _dispatch_callback_query
        from unittest.mock import patch, MagicMock, AsyncMock

        update = MagicMock()
        update.callback_query.data = "perm_remove_confirm:888899"
        update.callback_query.answer = AsyncMock()
        update.effective_user.id = 123456789
        context = MagicMock()

        with patch("bot.handlers.is_owner", return_value=True), \
             patch("bot.handlers.get_workspace_context", return_value=MagicMock(workspace_id=None)), \
             patch("database.queries.remove_workspace_member") as mock_remove:
            await _dispatch_callback_query(update, context)

            mock_remove.assert_not_called()
            update.callback_query.answer.assert_called_with("⚠️ Could not determine workspace.", show_alert=True)

    asyncio.run(_run())


def test_feature_allowed_no_mock_bypass():
    from unittest.mock import Mock
    from bot.feature_registry import feature_allowed

    # Passing Mock() objects should follow normal path and return False (not bypass to True)
    assert feature_allowed(Mock(), Mock(), "budget") is False


def test_unscoped_cafeteria_menu_reads_leak_prevention():
    from database.db import get_custom_menu_items
    from services.cafeteria_service import add_custom_menu_item
    from database.queries import get_or_create_workspace

    ws_a = get_or_create_workspace(title="Fix7 Cafe A", creator_user_id=7701, chat_id=7701, chat_type="dm").id
    ws_b = get_or_create_workspace(title="Fix7 Cafe B", creator_user_id=7702, chat_id=7702, chat_type="dm").id

    add_custom_menu_item("Idli Sambar A", 40.0, workspace_id=ws_a)
    add_custom_menu_item("Vada Pav B", 30.0, workspace_id=ws_b)

    items_a = get_custom_menu_items(workspace_id=ws_a)
    names_a = [it['name'] for it in items_a]
    assert "Idli Sambar A" in names_a
    assert "Vada Pav B" not in names_a

    # workspace_id=None must fail closed and return []
    assert get_custom_menu_items(workspace_id=None) == []


def test_recurring_writes_require_workspace_and_scope():
    import pytest
    from datetime import date
    from services.recurring_service import (
        add_recurring_payment,
        skip_recurring_due,
        update_recurring_status,
        delete_recurring_payment,
    )
    from database.queries import get_or_create_workspace

    ws_real = get_or_create_workspace(title="Fix8 Rec Real", creator_user_id=8801, chat_id=8801, chat_type="dm").id
    ws_other = get_or_create_workspace(title="Fix8 Rec Other", creator_user_id=8802, chat_id=8802, chat_type="dm").id

    rec_id = add_recurring_payment(
        payee_name="Fix8 Internet",
        amount=999.0,
        frequency="MONTHLY",
        start_date=date(2026, 10, 1),
        workspace_id=ws_real
    )

    # 1. Calling with workspace_id=None must raise ValueError
    with pytest.raises(ValueError):
        update_recurring_status(rec_id, "PAUSED", workspace_id=None)

    with pytest.raises(ValueError):
        delete_recurring_payment(rec_id, workspace_id=None)

    with pytest.raises(ValueError):
        skip_recurring_due(rec_id, workspace_id=None)

    # 2. Calling with mismatched workspace must result in 0 rows updated (return False)
    assert update_recurring_status(rec_id, "PAUSED", workspace_id=ws_other) is False
    assert delete_recurring_payment(rec_id, workspace_id=ws_other) is False


def test_restore_soft_deleted_transaction_fails_closed_without_workspace():
    from database.db import get_db_connection
    from database.models import Transaction
    from database.queries import insert_transaction, restore_soft_deleted_transaction, get_default_workspace_id

    default_ws = get_default_workspace_id()
    t = Transaction(
        amount=120.0,
        transaction_type="SENT",
        person_name="Fix9 Null WS",
        transaction_date="2026-10-11",
        workspace_id=default_ws
    )
    tx_id = insert_transaction(t)

    # Soft-delete and set workspace_id = NULL
    with get_db_connection() as conn:
        conn.cursor().execute("UPDATE transactions SET deleted_at = '2026-10-11T00:00:00Z', workspace_id = NULL WHERE id = ?", (tx_id,))
        conn.commit()

    # Attempt restore
    result = restore_soft_deleted_transaction(tx_id=tx_id)
    assert result is False, "Expected restore to return False for row lacking workspace_id"

    # Verify row was untouched (still soft-deleted and NULL workspace)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT deleted_at, workspace_id FROM transactions WHERE id = ?", (tx_id,))
        row = cur.fetchone()
        assert row['deleted_at'] == '2026-10-11T00:00:00Z'
        assert row['workspace_id'] is None








