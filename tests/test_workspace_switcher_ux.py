import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery

from database.db import setup_database
from database.queries import (
    get_or_create_workspace,
    get_workspace_by_id,
    get_workspace_member,
    add_workspace_member,
    ensure_all_user_workspaces,
    get_all_active_workspaces,
    create_custom_workspace
)
from bot.auth import (
    get_workspace_context,
    set_user_active_workspace,
    get_user_active_workspace
)
from bot.commands import workspace_command, render_workspaces_view
from bot.handlers import handle_callback_query


@pytest.fixture(autouse=True)
def init_db():
    setup_database()


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "supergroup", text: str = "", chat_title: str = "Payment"):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.full_name = f"Name_{user_id}"
    user.first_name = f"First_{user_id}"
    update.effective_user = user
    
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    chat.title = chat_title
    update.effective_chat = chat

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.chat_id = chat_id
    msg.message_id = 101
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


def test_auto_provisioning_and_friend_workspace_discovery():
    """Verifies that running ensure_all_user_workspaces auto-provisions friend Nagendra & owner Pranav personal ledgers."""
    owner_id = 8379948573
    friend_id = 8343764796
    group_chat_id = -1004310685141

    # Initially create the group workspace
    ws_group = get_or_create_workspace(
        chat_id=group_chat_id,
        chat_type="supergroup",
        title="Primary Workspace",
        creator_user_id=owner_id,
        username="pranav",
        display_name="Pranav"
    )

    # Run auto-provisioning
    with patch("config.TELEGRAM_USER_ID", owner_id):
        ensure_all_user_workspaces(current_chat_title="Payment", current_chat_id=group_chat_id)

    workspaces = get_all_active_workspaces()
    titles = [w.title for w in workspaces]

    # Verify group workspace renamed cleanly to Payment (Group)
    assert any("Payment (Group)" in t for t in titles)

    # Verify Pranav (Personal) and Nagendra (Personal) exist
    assert any("Pranav (Personal)" in t for t in titles)
    assert any("Nagendra (Personal)" in t for t in titles)


def test_group_chat_workspace_switching_and_reset():
    """Verifies that an owner in a group chat can switch active workspace to their personal ledger or friend's ledger."""
    async def _test():
        owner_id = 8379948573
        friend_id = 8343764796
        group_chat_id = -1004310685141

        with patch("config.TELEGRAM_USER_ID", owner_id):
            # Setup group and ledgers
            ws_grp = get_or_create_workspace(chat_id=group_chat_id, chat_type="supergroup", title="Payment (Group)", creator_user_id=owner_id)
            ws_friend = get_or_create_workspace(chat_id=friend_id, chat_type="dm", title="Nagendra (Personal)", creator_user_id=friend_id)

            up = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="supergroup", text="/workspaces")
            
            # 1. Initially in group chat, active workspace is the group workspace
            set_user_active_workspace(owner_id, None)
            ctx = get_workspace_context(up)
            assert ctx.workspace_id == ws_grp.id

            # 2. Owner switches active workspace to friend's workspace from group chat
            set_user_active_workspace(owner_id, ws_friend.id)
            ctx_switched = get_workspace_context(up)
            assert ctx_switched.workspace_id == ws_friend.id
            assert ctx_switched.workspace.title == "Nagendra (Personal)"

            # 3. Owner resets active workspace
            set_user_active_workspace(owner_id, None)
            ctx_reset = get_workspace_context(up)
            assert ctx_reset.workspace_id == ws_grp.id

    asyncio.run(_test())


def test_workspaces_ui_rendering_no_raw_uuids():
    """Verifies that render_workspaces_view generates a clean, rich UI with Group and Personal categories and no raw UUIDs."""
    owner_id = 8379948573
    group_chat_id = -1004310685141

    with patch("config.TELEGRAM_USER_ID", owner_id):
        get_or_create_workspace(chat_id=group_chat_id, chat_type="supergroup", title="Payment (Group)", creator_user_id=owner_id)
        get_or_create_workspace(chat_id=8343764796, chat_type="dm", title="Nagendra (Personal)", creator_user_id=8343764796)

        up = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="supergroup", text="/workspaces")
        text, markup = render_workspaces_view(up)

        # UI Checks
        assert "Workspace Information & Ledger Switcher" in text
        assert "Group Ledgers:" in text
        assert "Personal Ledgers:" in text
        assert "Payment (Group)" in text
        assert "Nagendra (Personal)" in text
        assert "Pranav (Personal)" in text

        # Verify no 36-character UUID strings in user-facing text
        import re
        uuid_pattern = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', re.I)
        assert not uuid_pattern.search(text), "Raw UUID found in user-facing workspaces text!"

        # Button checks
        all_btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("Payment (Group)" in b for b in all_btn_texts)
        assert any("Nagendra" in b for b in all_btn_texts)
        assert any("Reset to Chat Default" in b for b in all_btn_texts)
        assert any("New Ledger" in b for b in all_btn_texts)


def test_custom_workspace_creation():
    """Verifies that /workspace create <name> creates and switches to a custom standalone workspace."""
    async def _test():
        owner_id = 8379948573
        group_chat_id = -1004310685141

        with patch("config.TELEGRAM_USER_ID", owner_id):
            up = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="supergroup", text="/workspace create Goa Trip")
            context = MagicMock()
            context.args = ["create", "Goa", "Trip"]

            await workspace_command(up, context)
            up.message.reply_text.assert_called_once()
            reply = up.message.reply_text.call_args[0][0]
            assert "New Workspace Created" in reply
            assert "Goa Trip" in reply

            # Active workspace should now be the new Goa Trip workspace
            active_id = get_user_active_workspace(owner_id)
            assert active_id is not None
            ws = get_workspace_by_id(active_id)
            assert ws.title == "Goa Trip"

    asyncio.run(_test())


def test_owner_historical_transactions_auto_backfill_to_personal_workspace():
    """Verifies that owner's personal workspace inherits historical group transactions so it is not empty."""
    from database.queries import (
        get_default_workspace_id, get_transactions_paginated,
        get_workspace_by_chat_id, get_balance_setting
    )
    from database.db import get_db_connection
    from utils.dates import utc_now_iso
    import uuid

    owner_id = 8379948573
    default_ws_id = get_default_workspace_id()

    # Insert a dummy transaction into default workspace
    now_utc = utc_now_iso()
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO transactions (
                transaction_type, amount, person_name, transaction_date,
                category, balance_before, balance_after, uid, workspace_id, occurred_at, created_at, updated_at
            ) VALUES ('SENT', 500.0, 'Sai Akhil', '2026-10-01', 'General', 2000.0, 1500.0, ?, ?, ?, ?, ?)
        """, (uuid.uuid4().hex, default_ws_id, now_utc, now_utc, now_utc))
        conn.commit()

    with patch("config.TELEGRAM_USER_ID", owner_id):
        ensure_all_user_workspaces(current_chat_title="Payment", current_chat_id=-1004310685141)

    owner_ws = get_workspace_by_chat_id(owner_id)
    assert owner_ws is not None
    assert owner_ws.id != default_ws_id

    # Verify transactions in owner personal workspace
    tx_data = get_transactions_paginated(workspace_id=owner_ws.id)
    assert tx_data['total_count'] >= 1
    assert any(t['person_name'] == 'Sai Akhil' for t in tx_data['transactions'])

