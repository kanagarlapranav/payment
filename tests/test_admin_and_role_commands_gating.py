import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from telegram import Update, User, Chat, Message

from database.db import setup_database, get_db_connection
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    get_workspace_member
)
from bot.auth import COMMAND_ROLE_POLICY
from bot.commands import (
    removemember_command,
    setrole_command,
    demote_command
)


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "group", chat_title: str = "Role Test Group"):
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
    msg.text = ""
    msg.chat_id = chat_id
    msg.message_id = 101
    msg.reply_text = AsyncMock()
    msg.from_user = user
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


@pytest.fixture(autouse=True)
def setup_db():
    setup_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members WHERE telegram_user_id IN (9901, 9902, 9903)")
        conn.commit()


def test_command_role_policy_removemember_is_owner():
    """Error 9: COMMAND_ROLE_POLICY for removemember must be 'owner' matching the panel."""
    assert COMMAND_ROLE_POLICY.get("removemember") == "owner"


def test_removemember_command_gated_by_owner_only():
    """Error 9: Admin calling /removemember is rejected; only Owner can remove members."""
    async def _test():
        owner_id = 9901
        admin_id = 9902
        target_uid = 9903
        group_chat_id = -100990001

        ws = get_or_create_workspace(
            title="Removemember Gating Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, admin_id, role="admin")
        add_workspace_member(ws.id, target_uid, role="member")

        # Admin calls /removemember
        u_admin = make_mock_update(user_id=admin_id, chat_id=group_chat_id, chat_type="group")
        ctx = MagicMock()
        ctx.args = [str(target_uid)]

        await removemember_command(u_admin, ctx)
        # Should be rejected with access denied (not removed)
        assert get_workspace_member(ws.id, target_uid) is not None, "Admin must NOT be allowed to remove member"
        assert u_admin.message.reply_text.called
        denied_msg = u_admin.message.reply_text.call_args[0][0]
        assert "Access Denied" in denied_msg or "owner" in denied_msg.lower()

    asyncio.run(_test())


def test_setrole_and_demote_single_owner_gate():
    """Error 10: /setrole and /demote use a single require_owner gate."""
    async def _test():
        owner_id = 9901
        admin_id = 9902
        target_uid = 9903
        group_chat_id = -100990002

        ws = get_or_create_workspace(
            title="Single Gate Test Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, admin_id, role="admin")
        add_workspace_member(ws.id, target_uid, role="member")

        u_admin = make_mock_update(user_id=admin_id, chat_id=group_chat_id, chat_type="group")
        ctx = MagicMock()
        ctx.args = [str(target_uid), "admin"]

        # Admin calls setrole
        await setrole_command(u_admin, ctx)
        assert u_admin.message.reply_text.called
        reply = u_admin.message.reply_text.call_args[0][0]
        # Must be rejected by owner gate directly
        assert "Access Denied" in reply or "restricted" in reply or "owner" in reply.lower()

        # Admin calls demote
        u_admin.message.reply_text.reset_mock()
        ctx.args = [str(target_uid)]
        await demote_command(u_admin, ctx)
        assert u_admin.message.reply_text.called
        reply2 = u_admin.message.reply_text.call_args[0][0]
        assert "Access Denied" in reply2 or "restricted" in reply2 or "owner" in reply2.lower()

    asyncio.run(_test())

