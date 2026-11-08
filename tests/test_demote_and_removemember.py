import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from database.db import setup_database, get_db_connection
from database.queries import (
    get_or_create_workspace, add_workspace_member,
    get_workspace_member, remove_workspace_member
)
from bot.commands import demote_command, removemember_command
from telegram import Update, User, Chat, Message


@pytest.fixture(autouse=True)
def clean_db():
    setup_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members")
        conn.execute("DELETE FROM workspaces")
        conn.commit()
    yield
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members")
        conn.execute("DELETE FROM workspaces")
        conn.commit()


def make_update(user_id: int, chat_id: int, text: str = ""):
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"Name_{user_id}"
    user.full_name = f"Name_{user_id}"
    update.effective_user = user

    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = "group"
    chat.title = "Test Group"
    update.effective_chat = chat

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.chat_id = chat_id
    msg.reply_text = AsyncMock()
    msg.reply_to_message = None
    update.message = msg
    update.effective_message = msg
    return update


def test_demote_command_removes_admin_rights():
    """Verify /demote demotes an admin to a regular member in the current workspace."""
    async def _run():
        owner_id = 9001
        admin_id = 9002
        chat_id = -100555001

        ws = get_or_create_workspace(title="Demote Test Group", creator_user_id=owner_id, chat_id=chat_id, chat_type="group")
        add_workspace_member(ws.id, owner_id, display_name="Owner", role="owner")
        add_workspace_member(ws.id, admin_id, display_name="Nagendra", username="nagendra", role="admin")

        # Verify initial role is admin
        m = get_workspace_member(ws.id, admin_id)
        assert m.role == "admin"

        update = make_update(owner_id, chat_id, "/demote Nagendra")
        ctx = MagicMock()
        ctx.args = ["Nagendra"]
        ctx.bot = MagicMock()

        with patch("config.TELEGRAM_USER_ID", str(owner_id)):
            await demote_command(update, ctx)

        assert update.message.reply_text.called
        reply = update.message.reply_text.call_args[0][0]
        assert "now a regular <b>MEMBER</b>" in reply

        # Check in DB that role is now member
        m_after = get_workspace_member(ws.id, admin_id)
        assert m_after.role == "member"

    asyncio.run(_run())


def test_removemember_by_display_name():
    """Verify /removemember works with display names (not just usernames)."""
    async def _run():
        owner_id = 9001
        member_id = 9003
        chat_id = -100555002

        ws = get_or_create_workspace(title="Remove Test Group", creator_user_id=owner_id, chat_id=chat_id, chat_type="group")
        add_workspace_member(ws.id, owner_id, display_name="Owner", role="owner")
        # Member has display_name 'Nagendra Kumar' but NO username
        add_workspace_member(ws.id, member_id, display_name="Nagendra Kumar", username="", role="member")

        update = make_update(owner_id, chat_id, "/removemember Nagendra")
        ctx = MagicMock()
        ctx.args = ["Nagendra"]
        ctx.bot = MagicMock()

        with patch("config.TELEGRAM_USER_ID", str(owner_id)):
            await removemember_command(update, ctx)

        assert update.message.reply_text.called
        reply = update.message.reply_text.call_args[0][0]
        assert "was removed from this workspace" in reply

        # Verify member was deleted from workspace
        m_after = get_workspace_member(ws.id, member_id)
        assert m_after is None

    asyncio.run(_run())

