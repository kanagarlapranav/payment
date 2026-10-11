import asyncio
from unittest.mock import MagicMock, AsyncMock
from bot.feature_registry import set_feature_permission
from database.queries import get_or_create_workspace, add_workspace_member

def test_four_remaining_home_buttons_gated_by_feature_matrix():
    """Fix 2: verify nav_today, nav_contacts, nav_digest, nav_backup_status enforce feature matrix."""
    from bot.callbacks.nav import nav_today, nav_contacts, nav_digest, nav_backup_status

    async def _run():
        ws = get_or_create_workspace(
            title="Test Gated Nav WS",
            creator_user_id=123456,
            chat_id=123456,
            chat_type="dm"
        )
        member_uid = 777001
        add_workspace_member(ws.id, member_uid, role="member")
        ws_ctx = MagicMock(role="member", workspace_id=ws.id)

        # Handlers and their mapped features
        test_cases = [
            (nav_today, "history"),
            (nav_contacts, "details"),
            (nav_digest, "report"),
            (nav_backup_status, "backup")
        ]

        for handler, feature_key in test_cases:
            # 1. Revoke the feature for member role
            set_feature_permission(ws.id, feature_key, "member", False)

            query = MagicMock()
            query.from_user.id = member_uid
            query.answer = AsyncMock()
            query.edit_message_text = AsyncMock()

            await handler(query, MagicMock(), ["nav", "dummy"], ws.id, ws_ctx)

            # Must reject with alert and not render
            query.answer.assert_called_once_with("⛔ This feature is disabled for your role.", show_alert=True)
            query.edit_message_text.assert_not_called()

    asyncio.run(_run())


def test_can_user_modify_transaction_workspace_scoping():
    """Fix 3: verify transaction in workspace A cannot be modified from workspace B."""
    from database.models import Transaction
    from database.queries import can_user_modify_transaction
    from services.transaction_service import commit_transaction

    ws_a = get_or_create_workspace(title="WS A", creator_user_id=888001, chat_id=888001, chat_type="dm")
    ws_b = get_or_create_workspace(title="WS B", creator_user_id=888002, chat_id=888002, chat_type="dm")

    member_a = 888101
    admin_a = 888102
    add_workspace_member(ws_a.id, member_a, role="member")
    add_workspace_member(ws_a.id, admin_a, role="admin")

    # Transaction in WS A created by member_a
    tx = Transaction(
        amount=150.0,
        transaction_type="SENT",
        person_name="Tea Stall",
        category="Food & Dining",
        transaction_date="2026-10-11",
        workspace_id=ws_a.id,
        telegram_user_id=member_a
    )
    commit_transaction(tx)
    tx_id = tx.id

    # Scoped to Workspace A -> caller can modify
    assert can_user_modify_transaction(tx_id, member_a, "member", workspace_id=ws_a.id) is True
    assert can_user_modify_transaction(tx_id, admin_a, "admin", workspace_id=ws_a.id) is True

    # Scoped to Workspace B -> caller CANNOT modify (must fail closed)
    assert can_user_modify_transaction(tx_id, member_a, "member", workspace_id=ws_b.id) is False
    assert can_user_modify_transaction(tx_id, admin_a, "admin", workspace_id=ws_b.id) is False

