import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery
from telegram.ext import ContextTypes
from database.db import setup_database, get_db_connection
from database.queries import (
    get_or_create_workspace, add_workspace_member, get_workspace_member,
    set_user_permission_and_role, get_default_workspace_id, get_all_workspace_members,
    get_all_users_for_permissions, get_transactions_paginated
)
from bot.auth import (
    is_owner, is_authorized_user, require_member, require_admin,
    get_workspace_context, set_user_active_workspace, _USER_ACTIVE_WORKSPACES
)
from bot.handlers import handle_text, handle_callback_query
import config

OWNER_ID = 8379948573
NAGENDRA_ID = 8343764796
GROUP_CHAT_ID = -1004310685141


@pytest.fixture(autouse=True)
def setup_test_state():
    setup_database()
    _USER_ACTIVE_WORKSPACES.clear()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_settings WHERE key LIKE 'user_active_ws:%'")
        conn.execute("DELETE FROM settings WHERE key LIKE 'user_active_ws:%'")
        conn.commit()


def make_group_update(user_id: int, text: str = "120 dosa", username: str = "user", display_name: str = "User"):
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = GROUP_CHAT_ID
    chat.type = "supergroup"
    chat.title = "Payment (Group)"

    user = MagicMock(spec=User)
    user.id = user_id
    user.username = username
    user.first_name = display_name
    user.full_name = display_name

    msg = MagicMock(spec=Message)
    msg.chat = chat
    msg.chat_id = GROUP_CHAT_ID
    msg.from_user = user
    msg.message_id = 9999
    msg.text = text
    msg.caption = None
    msg.photo = []
    msg.document = None
    msg.reply_text = AsyncMock()

    update.effective_chat = chat
    update.effective_user = user
    update.effective_message = msg
    update.message = msg
    update.callback_query = None
    return update


@pytest.mark.anyio
async def test_member_can_log_payment_without_admin_rejection():
    """Verifies that Nagendra (as member) can log payments via text in the group without admin-only notices."""
    ws = get_or_create_workspace(
        chat_id=GROUP_CHAT_ID,
        chat_type="supergroup",
        title="Payment (Group)",
        creator_user_id=OWNER_ID
    )
    add_workspace_member(ws.id, OWNER_ID, role="owner")
    add_workspace_member(ws.id, NAGENDRA_ID, username="nagendra", display_name="Nagendra", role="member")

    up = make_group_update(NAGENDRA_ID, text="120 dosa", username="nagendra", display_name="Nagendra")
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.user_data = {}

    with patch("config.TELEGRAM_USER_ID", OWNER_ID):
        await handle_text(up, ctx)

    # Must NOT reply with "Admin Only"
    for call in up.message.reply_text.call_args_list:
        text_arg = call.args[0] if call.args else call.kwargs.get("text", "")
        assert "Admin Only" not in text_arg, f"Unexpected Admin Only warning: {text_arg}"

    # Verify transaction was saved successfully
    txs = get_transactions_paginated(workspace_id=ws.id)["transactions"]
    saved = next((t for t in txs if t["amount"] == 120.0 and "Dosa" in (t["person_name"] or "")), None)
    assert saved is not None
    assert saved["telegram_user_id"] == NAGENDRA_ID


@pytest.mark.anyio
async def test_owner_can_promote_nagendra_to_admin_and_verify_powers():
    """Verifies that owner clicking Admin button in permissions dashboard promotes Nagendra and allows admin actions."""
    ws = get_or_create_workspace(
        chat_id=GROUP_CHAT_ID,
        chat_type="supergroup",
        title="Payment (Group)",
        creator_user_id=OWNER_ID
    )
    add_workspace_member(ws.id, OWNER_ID, role="owner")
    add_workspace_member(ws.id, NAGENDRA_ID, username="nagendra", display_name="Nagendra", role="member")

    # 1. Owner triggers perm_set callback to promote Nagendra to admin
    up = MagicMock(spec=Update)
    query = MagicMock(spec=CallbackQuery)
    query.data = f"perm_set:{NAGENDRA_ID}:admin"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.edit_message_text = AsyncMock()

    owner_user = MagicMock(spec=User)
    owner_user.id = OWNER_ID
    owner_user.username = "pranav"
    query.from_user = owner_user

    up.callback_query = query
    up.effective_user = owner_user
    up.effective_chat = MagicMock(spec=Chat)
    up.effective_chat.id = OWNER_ID
    up.effective_chat.type = "private"
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("config.TELEGRAM_USER_ID", OWNER_ID):
        await handle_callback_query(up, ctx)

    # 2. Check DB: Nagendra is now admin in the workspace
    m = get_workspace_member(ws.id, NAGENDRA_ID)
    assert m is not None
    assert m.role == "admin"

    # 3. Verify Nagendra now satisfies require_admin in the group
    nag_group_up = make_group_update(NAGENDRA_ID, text="/audit")
    with patch("config.TELEGRAM_USER_ID", OWNER_ID):
        assert await require_admin(nag_group_up) is True


@pytest.mark.anyio
async def test_group_messages_route_to_group_workspace_by_default():
    """Verifies that group messages route to the group workspace by default."""
    group_ws = get_or_create_workspace(
        chat_id=GROUP_CHAT_ID,
        chat_type="supergroup",
        title="Payment (Group)",
        creator_user_id=OWNER_ID
    )
    add_workspace_member(group_ws.id, NAGENDRA_ID, role="member")

    group_up = make_group_update(NAGENDRA_ID, text="50 tea")
    with patch("config.TELEGRAM_USER_ID", OWNER_ID):
        ws_ctx = get_workspace_context(group_up)
        assert ws_ctx.workspace_id == group_ws.id
        assert ws_ctx.chat_id == GROUP_CHAT_ID


@pytest.mark.anyio
async def test_global_error_handler_handles_update_object_safely():
    """Verifies that app.global_error_handler does not throw NameError on Update."""
    import app
    up = make_group_update(NAGENDRA_ID, text="test")
    ctx = MagicMock()
    ctx.error = RuntimeError("Simulated transient error")
    
    # Should execute without throwing NameError: name 'Update' is not defined
    await app.global_error_handler(up, ctx)
    up.effective_message.reply_text.assert_called_once()
    assert "unexpected error occurred" in up.effective_message.reply_text.call_args[0][0]

