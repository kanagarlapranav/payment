import pytest
import asyncio
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery

from database.db import setup_database, get_db_connection
from database.queries import (
    get_or_create_workspace,
    get_workspace_by_id,
    get_workspace_member,
    add_workspace_member,
    ensure_all_user_workspaces,
    get_all_active_workspaces,
    create_custom_workspace,
    get_default_workspace_id
)
from database.models import Transaction
from services.transaction_service import commit_transaction
from bot.auth import (
    get_workspace_context,
    set_user_active_workspace,
    get_user_active_workspace,
    _USER_ACTIVE_WORKSPACES
)
from bot.commands import workspace_command, render_workspaces_view
from bot.handlers import handle_callback_query


SYNTHETIC_OWNER_ID = 1000000001
SYNTHETIC_FRIEND_ID = 8343764796
SYNTHETIC_GROUP_CHAT_ID = -1001234567890
SYNTHETIC_MEMBER_ID = 2000000002


@pytest.fixture(autouse=True)
def init_db():
    test_dir = tempfile.mkdtemp()
    db_path = os.path.join(test_dir, "test_switcher.db")
    db_patch = patch("database.db.DB_PATH", db_path)
    db_patch2 = patch("config.DB_PATH", db_path)
    db_patch.start()
    db_patch2.start()

    setup_database()
    _USER_ACTIVE_WORKSPACES.clear()

    yield

    _USER_ACTIVE_WORKSPACES.clear()
    db_patch2.stop()
    db_patch.stop()
    shutil.rmtree(test_dir, ignore_errors=True)


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
    """Verifies that running ensure_all_user_workspaces auto-provisions friend & owner personal ledgers."""
    owner_id = SYNTHETIC_OWNER_ID
    friend_id = SYNTHETIC_FRIEND_ID
    group_chat_id = SYNTHETIC_GROUP_CHAT_ID

    # Initially create the group workspace
    ws_group = get_or_create_workspace(
        chat_id=group_chat_id,
        chat_type="supergroup",
        title="Primary Workspace",
        creator_user_id=owner_id,
        username="owner",
        display_name="Owner"
    )

    # Run auto-provisioning
    with patch("config.TELEGRAM_USER_ID", owner_id):
        ensure_all_user_workspaces(current_chat_title="Payment", current_chat_id=group_chat_id)

    workspaces = get_all_active_workspaces()
    titles = [w.title for w in workspaces]

    # Verify group workspace renamed cleanly to Payment (Group)
    assert any("Payment (Group)" in t for t in titles)

    # Verify Personal ledger exists for friend
    assert any("Nagendra (Personal)" in t for t in titles)


def test_group_chat_workspace_switching_and_reset():
    """Verifies that an owner in a group chat always resolves to the group workspace (DM switch does not leak to group)."""
    async def _test():
        owner_id = SYNTHETIC_OWNER_ID
        member_id = SYNTHETIC_MEMBER_ID
        group_chat_id = SYNTHETIC_GROUP_CHAT_ID

        with patch("config.TELEGRAM_USER_ID", owner_id):
            # Setup group and ledgers
            ws_grp = get_or_create_workspace(chat_id=group_chat_id, chat_type="supergroup", title="Payment (Group)", creator_user_id=owner_id)
            ws_friend = get_or_create_workspace(chat_id=member_id, chat_type="dm", title="Member (Personal)", creator_user_id=member_id)

            up_grp = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="supergroup", text="/workspaces")
            up_dm = make_mock_update(user_id=owner_id, chat_id=owner_id, chat_type="private", text="/workspaces")
            
            # 1. Initially in group chat, active workspace is the group workspace
            set_user_active_workspace(owner_id, None)
            ctx = get_workspace_context(up_grp)
            assert ctx.workspace_id == ws_grp.id

            # 2. Owner sets active workspace (in DM): group chat MUST NOT be overridden
            set_user_active_workspace(owner_id, ws_friend.id)
            ctx_grp = get_workspace_context(up_grp)
            assert ctx_grp.workspace_id == ws_grp.id

            # In private DM, active workspace is honored
            ctx_dm = get_workspace_context(up_dm)
            assert ctx_dm.workspace_id == ws_friend.id
            assert ctx_dm.workspace.title == "Member (Personal)"

            # 3. Owner resets active workspace
            set_user_active_workspace(owner_id, None)
            ctx_reset = get_workspace_context(up_grp)
            assert ctx_reset.workspace_id == ws_grp.id

    asyncio.run(_test())


def test_workspaces_ui_rendering_no_raw_uuids():
    """Verifies that render_workspaces_view generates a clean, rich UI with Group and Personal categories and no raw UUIDs."""
    owner_id = SYNTHETIC_OWNER_ID
    group_chat_id = SYNTHETIC_GROUP_CHAT_ID
    member_id = SYNTHETIC_MEMBER_ID

    with patch("config.TELEGRAM_USER_ID", owner_id):
        get_or_create_workspace(chat_id=group_chat_id, chat_type="supergroup", title="Payment (Group)", creator_user_id=owner_id)
        get_or_create_workspace(chat_id=member_id, chat_type="dm", title="Member (Personal)", creator_user_id=member_id)
        ws_p = get_or_create_workspace(chat_id=3000000003, chat_type="dm", title="Secondary (Personal)", creator_user_id=owner_id)
        add_workspace_member(ws_p.id, owner_id, role="owner")

        up = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="supergroup", text="/workspaces")
        text, markup = render_workspaces_view(up)

        # UI Checks
        assert "Workspace Information & Ledger Switcher" in text
        assert "Group Ledgers:" in text
        assert "Personal Ledgers:" in text
        assert "Payment (Group)" in text
        assert "Member (Personal)" in text
        assert "Secondary (Personal)" in text

        # Verify no 36-character UUID strings in user-facing text
        import re
        uuid_pattern = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', re.I)
        assert not uuid_pattern.search(text), "Raw UUID found in user-facing workspaces text!"

        # Button checks
        all_btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("Payment (Group)" in b for b in all_btn_texts)
        assert any("Member" in b for b in all_btn_texts)
        assert any("Reset to Chat Default" in b for b in all_btn_texts)
        assert any("New Ledger" in b for b in all_btn_texts)


def test_custom_workspace_creation():
    """Verifies that /workspace create <name> creates and switches to a custom standalone workspace."""
    async def _test():
        owner_id = SYNTHETIC_OWNER_ID
        group_chat_id = SYNTHETIC_GROUP_CHAT_ID

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

    owner_id = SYNTHETIC_OWNER_ID
    group_chat_id = SYNTHETIC_GROUP_CHAT_ID

    with patch("config.TELEGRAM_USER_ID", owner_id):
        ensure_all_user_workspaces(current_chat_title="Payment", current_chat_id=group_chat_id)

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


def test_restricted_member_is_strictly_member_and_cannot_be_owner():
    """Verifies that restricted user cannot be owner, but can be promoted to admin by owner."""
    from bot.auth import is_owner
    from database.queries import (
        update_workspace_member_role, get_workspace_member,
        add_workspace_member, get_default_workspace_id
    )

    restricted_id = SYNTHETIC_FRIEND_ID
    ws_id = get_default_workspace_id()

    # Seed member
    add_workspace_member(ws_id, restricted_id, role="member")

    # In auth check
    up = make_mock_update(user_id=restricted_id, chat_id=SYNTHETIC_GROUP_CHAT_ID, chat_type="supergroup")
    assert not is_owner(up, workspace_id=ws_id)

    # In database queries: admin role is allowed
    success = update_workspace_member_role(ws_id, restricted_id, "admin")
    assert success is True
    m = get_workspace_member(ws_id, restricted_id)
    assert m.role == 'admin'

    # Owner role is rejected
    with pytest.raises(ValueError, match="cannot be assigned the owner role"):
        update_workspace_member_role(ws_id, restricted_id, "owner")

    # Add member caps at member if role='owner' attempted
    m = add_workspace_member(ws_id, restricted_id, role="owner")
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
        chat_id = ws.chat_id if (ws and ws.chat_id) else SYNTHETIC_GROUP_CHAT_ID
        with get_db_connection() as conn:
            conn.execute("UPDATE workspaces SET chat_id = ? WHERE id = ?", (chat_id, ws_id))
            conn.commit()

        owner_id = SYNTHETIC_OWNER_ID
        add_workspace_member(ws_id, owner_id, username="owner", display_name="Owner", role="owner")
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


def test_switch_workspace_returns_target_workspace_query_rows():
    """
    Verifies that switching workspace actually causes subsequent queries
    (get_recent_transactions, search_transactions, get_overall_summary)
    to return rows belonging strictly to the target workspace.
    """
    from database.queries import (
        get_recent_transactions, search_transactions, create_custom_workspace
    )
    from services.balance_service import get_overall_summary

    owner_id = SYNTHETIC_OWNER_ID

    with patch("config.TELEGRAM_USER_ID", owner_id):
        # 1. Create two distinct workspaces
        ws1 = create_custom_workspace("Workspace Alpha", creator_user_id=owner_id)
        ws2 = create_custom_workspace("Workspace Beta", creator_user_id=owner_id)

        # 2. Seed transactions into both workspaces
        t1 = Transaction(
            amount=100.0,
            transaction_type="SENT",
            person_name="Vendor Alpha",
            category="Supplies",
            transaction_date="2026-10-01",
            workspace_id=ws1.id
        )
        commit_transaction(t1)

        t2 = Transaction(
            amount=200.0,
            transaction_type="SENT",
            person_name="Vendor Beta",
            category="Utilities",
            transaction_date="2026-10-01",
            workspace_id=ws2.id
        )
        commit_transaction(t2)

        up_dm = make_mock_update(user_id=owner_id, chat_id=owner_id, chat_type="private")

        # 3. Switch to Workspace Alpha
        set_user_active_workspace(owner_id, ws1.id)
        ctx1 = get_workspace_context(up_dm)
        assert ctx1 is not None
        assert ctx1.workspace_id == ws1.id

        # Query using the active workspace context
        txs_alpha = get_recent_transactions(limit=10, workspace_id=ctx1.workspace_id)
        alpha_merchants = [tx['person_name'] for tx in txs_alpha]
        assert "Vendor Alpha" in alpha_merchants
        assert "Vendor Beta" not in alpha_merchants

        search_alpha = search_transactions(query_text="Vendor", workspace_id=ctx1.workspace_id)
        assert len(search_alpha) == 1
        assert search_alpha[0]['person_name'] == "Vendor Alpha"

        summary_alpha = get_overall_summary(workspace_id=ctx1.workspace_id)
        assert summary_alpha.total_sent == 100.0

        # 4. Switch to Workspace Beta
        set_user_active_workspace(owner_id, ws2.id)
        ctx2 = get_workspace_context(up_dm)
        assert ctx2 is not None
        assert ctx2.workspace_id == ws2.id

        # Query using the newly switched active workspace context
        txs_beta = get_recent_transactions(limit=10, workspace_id=ctx2.workspace_id)
        beta_merchants = [tx['person_name'] for tx in txs_beta]
        assert "Vendor Beta" in beta_merchants
        assert "Vendor Alpha" not in beta_merchants

        search_beta = search_transactions(query_text="Vendor", workspace_id=ctx2.workspace_id)
        assert len(search_beta) == 1
        assert search_beta[0]['person_name'] == "Vendor Beta"

        summary_beta = get_overall_summary(workspace_id=ctx2.workspace_id)
        assert summary_beta.total_sent == 200.0
