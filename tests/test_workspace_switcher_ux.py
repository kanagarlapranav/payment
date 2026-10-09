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
    from bot.auth import _USER_ACTIVE_WORKSPACES
    _USER_ACTIVE_WORKSPACES.clear()
    from database.db import get_db_connection
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_settings WHERE key LIKE 'user_active_ws:%'")
        conn.execute("DELETE FROM settings WHERE key LIKE 'user_active_ws:%'")
        conn.commit()
    yield
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspaces WHERE id != (SELECT value FROM settings WHERE key = 'default_workspace_id')")
        conn.execute("DELETE FROM workspace_members WHERE workspace_id != (SELECT value FROM settings WHERE key = 'default_workspace_id')")
        conn.execute("DELETE FROM workspace_settings WHERE key LIKE 'user_active_ws:%'")
        conn.execute("DELETE FROM settings WHERE key LIKE 'user_active_ws:%'")
        conn.commit()
    _USER_ACTIVE_WORKSPACES.clear()


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

    # Verify Nagendra (Personal) exists and Nagendra is strictly member
    assert any("Nagendra (Personal)" in t for t in titles)
    # Verify Pranav personal workspace was skipped/removed per user requirement
    assert not any("Pranav (Personal)" in t for t in titles)


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
        ws_p = get_or_create_workspace(chat_id=99887766, chat_type="dm", title="Pranav (Personal)", creator_user_id=owner_id)
        add_workspace_member(ws_p.id, owner_id, role="owner")

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


def test_owner_uses_payment_group_workspace_directly():
    """Verifies that owner does not create a personal duplicate workspace and uses Payment (Group) directly in private DMs."""
    from database.queries import (
        get_default_workspace_id, get_workspace_by_chat_id
    )

    owner_id = 8379948573

    with patch("config.TELEGRAM_USER_ID", owner_id):
        ensure_all_user_workspaces(current_chat_title="Payment", current_chat_id=-1004310685141)

    default_ws_id = get_default_workspace_id()

    # Verify no personal workspace exists for owner
    owner_ws = get_workspace_by_chat_id(owner_id)
    assert owner_ws is None

    # Verify DM context for owner routes directly to group workspace
    up_dm = make_mock_update(user_id=owner_id, chat_id=owner_id, chat_type="private")
    with patch("config.TELEGRAM_USER_ID", owner_id):
        ctx = get_workspace_context(up_dm)
        assert ctx.workspace_id == default_ws_id
        assert ctx.role == 'owner'


def test_nagendra_is_strictly_member_and_cannot_be_owner():
    """Verifies that Nagendra cannot be owner, but can be promoted to admin by owner."""
    from bot.auth import is_owner
    from database.queries import (
        update_workspace_member_role, get_workspace_member,
        add_workspace_member, get_default_workspace_id
    )

    nagendra_id = 8343764796
    ws_id = get_default_workspace_id()

    # In auth check
    up = make_mock_update(user_id=nagendra_id, chat_id=-1004310685141, chat_type="supergroup")
    assert not is_owner(up, workspace_id=ws_id)

    # In database queries: admin role is allowed
    success = update_workspace_member_role(ws_id, nagendra_id, "admin")
    assert success is True
    m = get_workspace_member(ws_id, nagendra_id)
    assert m.role == 'admin'

    # Owner role is rejected
    with pytest.raises(ValueError, match="Nagendra cannot be assigned the owner role"):
        update_workspace_member_role(ws_id, nagendra_id, "owner")

    # Add member caps at member if role='owner' attempted
    m = add_workspace_member(ws_id, nagendra_id, role="owner")
    assert m.role == 'member'


def test_remove_workspace_member():
    """Verifies that workspace members can be removed via queries and /removemember command."""
    from database.queries import (
        add_workspace_member, get_workspace_member,
        remove_workspace_member, get_default_workspace_id
    )
    from bot.commands import removemember_command

    ws_id = get_default_workspace_id()
    test_user_id = 999888777

    # Add member
    add_workspace_member(ws_id, test_user_id, username="testuser", display_name="Test User", role="member")
    assert get_workspace_member(ws_id, test_user_id) is not None

    # Remove member via query
    assert remove_workspace_member(ws_id, test_user_id) is True
    assert get_workspace_member(ws_id, test_user_id) is None

    # Test /removemember command
    async def _test_cmd():
        ws = get_workspace_by_id(ws_id)
        chat_id = ws.chat_id if (ws and ws.chat_id) else -1004310685141
        from database.db import get_db_connection
        with get_db_connection() as conn:
            conn.execute("UPDATE workspaces SET chat_id = ? WHERE id = ?", (chat_id, ws_id))
            conn.commit()

        owner_id = 8379948573
        add_workspace_member(ws_id, owner_id, username="pranav", display_name="Pranav", role="owner")
        add_workspace_member(ws_id, test_user_id, username="testuser", display_name="Test User", role="member")
        up = make_mock_update(user_id=owner_id, chat_id=chat_id, chat_type="supergroup")
        ctx_mock = MagicMock()
        ctx_mock.args = ["999888777"]

        with patch("config.TELEGRAM_USER_ID", owner_id):
            await removemember_command(up, ctx_mock)
            assert get_workspace_member(ws_id, test_user_id) is None
            up.message.reply_text.assert_called()
            reply = up.message.reply_text.call_args[0][0]
            assert "removed from this workspace" in reply

    asyncio.run(_test_cmd())

