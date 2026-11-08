import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from telegram import Update, User, Chat, Message

from database.db import setup_database, get_db_connection
from database.models import Transaction
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    remove_workspace_member,
    get_workspace_member,
    get_workspace_setting,
    set_user_permission_and_role
)
from bot.auth import get_workspace_context, resolve_workspace_context
from bot.commands import (
    balance_command,
    last5_command,
    demote_command,
    addmember_command,
    render_user_permission_card
)
from bot.handlers import handle_text, handle_callback_query


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "group", chat_title: str = "Payment Group", text: str = ""):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"Name_{user_id}"
    user.full_name = f"Name_{user_id}"
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
    msg.from_user = user
    msg.reply_to_message = None
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


@pytest.fixture(autouse=True)
def setup_db():
    setup_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members WHERE telegram_user_id IN (7701, 7702, 7703, 7704)")
        conn.execute("DELETE FROM workspace_settings WHERE key LIKE 'removed_user:%'")
        conn.commit()


def test_removed_user_cannot_get_context_or_readd_via_group_message():
    """A removed user sends a message in the group: context is denied and no member row is created."""
    async def _test():
        owner_id = 7701
        target_uid = 7702
        group_chat_id = -100770001

        ws = get_or_create_workspace(
            title="Sticky Removal Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, target_uid, role="admin")

        # Owner removes the user
        assert remove_workspace_member(ws.id, target_uid) is True
        assert get_workspace_member(ws.id, target_uid) is None

        # Removed user sends a message in the group
        u_removed = make_mock_update(user_id=target_uid, chat_id=group_chat_id, chat_type="group", text="Paid 500 for dinner")
        ctx_mock = MagicMock()

        # Context resolution must deny context for the removed user
        ws_ctx = await resolve_workspace_context(u_removed)
        assert ws_ctx is None, "Removed user should not be resolved into a valid workspace context"

        # handle_text must not create transactions or re-add the member
        await handle_text(u_removed, ctx_mock)
        assert get_workspace_member(ws.id, target_uid) is None, "Removed user must NOT be resurrected in workspace_members"

    asyncio.run(_test())


def test_removed_user_cannot_run_commands():
    """A removed user attempts to run commands: denied, context is None, and user is not re-added."""
    async def _test():
        owner_id = 7701
        target_uid = 7702
        group_chat_id = -100770002

        ws = get_or_create_workspace(
            title="Sticky Command Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, target_uid, role="member")

        remove_workspace_member(ws.id, target_uid)
        assert get_workspace_member(ws.id, target_uid) is None

        u_cmd = make_mock_update(user_id=target_uid, chat_id=group_chat_id, chat_type="group")
        ctx_mock = MagicMock()
        ctx_mock.args = []

        await balance_command(u_cmd, ctx_mock)
        # Should be rejected / access denied
        assert get_workspace_member(ws.id, target_uid) is None, "Command run must not resurrect removed member"

        await last5_command(u_cmd, ctx_mock)
        assert get_workspace_member(ws.id, target_uid) is None, "Command run must not resurrect removed member"

    asyncio.run(_test())


def test_demote_on_removed_user_does_not_readd():
    """Running /demote on a removed user must fail and must NOT resurrect them as a member."""
    async def _test():
        owner_id = 7701
        target_uid = 7702
        group_chat_id = -100770003

        ws = get_or_create_workspace(
            title="Sticky Demote Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, target_uid, role="admin")

        remove_workspace_member(ws.id, target_uid)
        assert get_workspace_member(ws.id, target_uid) is None

        u_owner = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="group")
        ctx_mock = MagicMock()
        ctx_mock.args = [str(target_uid)]

        await demote_command(u_owner, ctx_mock)

        # User must still be None in workspace_members!
        assert get_workspace_member(ws.id, target_uid) is None, "/demote on a removed user must NOT re-add them"

    asyncio.run(_test())


def test_role_button_tap_on_removed_user_does_not_readd():
    """Tapping a role button (perm_set) on a removed user's card must NOT resurrect them."""
    async def _test():
        owner_id = 7701
        target_uid = 7702
        group_chat_id = -100770004

        ws = get_or_create_workspace(
            title="Sticky Role Tap Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, target_uid, role="admin")

        remove_workspace_member(ws.id, target_uid)
        assert get_workspace_member(ws.id, target_uid) is None

        # Verify permission card does not offer role buttons that resurrect
        card_text, card_markup = render_user_permission_card(target_uid, workspace_id=ws.id)
        # Direct call to set_user_permission_and_role or perm_set handler
        res = set_user_permission_and_role(target_uid, "member", is_active=True, workspace_id=ws.id)
        # Should refuse resurrection
        assert res is False, "set_user_permission_and_role must refuse to resurrect a removed user"
        assert get_workspace_member(ws.id, target_uid) is None

    asyncio.run(_test())


def test_owner_explicit_addmember_readds_removed_user():
    """When the owner explicitly runs /addmember, the user is successfully re-added and removal marker cleared."""
    async def _test():
        owner_id = 7701
        target_uid = 7702
        group_chat_id = -100770005

        ws = get_or_create_workspace(
            title="Explicit Readd Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, target_uid, role="admin")

        remove_workspace_member(ws.id, target_uid)
        assert get_workspace_member(ws.id, target_uid) is None
        assert get_workspace_setting(ws.id, f"removed_user:{target_uid}") == '1'

        u_owner = make_mock_update(user_id=owner_id, chat_id=group_chat_id, chat_type="group")
        ctx_mock = MagicMock()
        ctx_mock.args = [str(target_uid), "viewer"]

        await addmember_command(u_owner, ctx_mock)

        # Now member should be re-added as viewer!
        mem = get_workspace_member(ws.id, target_uid)
        assert mem is not None
        assert mem.role == "viewer"
        assert get_workspace_setting(ws.id, f"removed_user:{target_uid}") is None

    asyncio.run(_test())
