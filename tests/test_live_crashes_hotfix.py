import asyncio
from unittest.mock import MagicMock, AsyncMock
from bot.handlers import handle_callback_query
from database.queries import get_or_create_workspace
from bot.auth import get_user_active_workspace, set_user_active_workspace
import config

def test_ws_switch_unbound_local_error():
    """Fix 1: Tapping ws_switch must not raise UnboundLocalError for get_default_workspace_id."""
    async def _run():
        owner_id = int(config.TELEGRAM_USER_ID) if config.TELEGRAM_USER_ID else 123456789
        from database.queries import get_default_workspace_id
        def_ws_id = get_default_workspace_id()

        target_ws = get_or_create_workspace(
            title="Target Workspace",
            creator_user_id=owner_id,
            chat_id=999888777,
            chat_type="group"
        )
        if target_ws.id == def_ws_id:
            target_ws = get_or_create_workspace(
                title="Secondary Workspace",
                creator_user_id=owner_id,
                chat_id=999888778,
                chat_type="group"
            )
        # Ensure active workspace is cleared first
        set_user_active_workspace(owner_id, None)

        update = MagicMock()
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"

        query = MagicMock()
        query.data = f"ws_switch:{target_ws.id}"
        query.from_user.id = owner_id
        query.message = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        # Must execute without UnboundLocalError and successfully switch
        try:
            await handle_callback_query(update, context)
            assert get_user_active_workspace(owner_id) == target_ws.id
        finally:
            set_user_active_workspace(owner_id, None)

    asyncio.run(_run())



def test_is_admin_user_and_undo_action_for_non_owner():
    """Fix 2: is_admin_user works without NameError for non-owner and undo_action is reachable."""
    from bot.commands import is_admin_user

    mock_update = MagicMock()
    mock_update.effective_user.id = 987654321
    # Non-owner user returns False without raising NameError
    assert is_admin_user(mock_update) is False

    async def _run_undo():
        member_id = 987654321
        update = MagicMock()
        update.effective_user.id = member_id
        update.effective_chat.id = member_id
        update.effective_chat.type = "private"

        query = MagicMock()
        query.data = "undo_action"
        query.from_user.id = member_id
        query.message = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        # Should reach authorization decision (no NameError)
        await handle_callback_query(update, context)

    asyncio.run(_run_undo())


def test_cap_panels_render_and_command():
    """Fix 3: render_cap_tx_panel, render_cap_month_panel and /settings cap_tx 500 must not raise ModuleNotFoundError."""
    from bot.commands import render_cap_tx_panel, render_cap_month_panel, settings_command
    from database.queries import get_workspace_setting, get_default_workspace_id

    # 1. Panels render without ModuleNotFoundError
    text_tx, markup_tx = render_cap_tx_panel()
    assert "Per-Transaction Spending Cap" in text_tx
    assert markup_tx is not None

    text_month, markup_month = render_cap_month_panel()
    assert "Monthly Spending Cap" in text_month
    assert markup_month is not None

    # 2. Run /settings cap_tx 500
    async def _run_cap():
        owner_id = int(config.TELEGRAM_USER_ID) if config.TELEGRAM_USER_ID else 123456789
        update = MagicMock()
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["cap_tx", "500"]

        await settings_command(update, context)
        update.message.reply_text.assert_called()
        reply_call_args = update.message.reply_text.call_args[0][0]
        assert "Per-transaction cap set to" in reply_call_args

        from bot.auth import get_workspace_context
        ctx = get_workspace_context(update)
        assert get_workspace_setting(ctx.workspace_id, "per_tx_cap") == "500.0"

    asyncio.run(_run_cap())

