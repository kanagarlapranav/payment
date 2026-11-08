import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from database.db import setup_database, get_db_connection
from database.queries import (
    get_or_create_workspace, add_workspace_member,
    get_workspace_member
)
from bot.commands import addmember_command
from bot.handlers import handle_new_chat_members
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
    chat.title = "Payment Group"
    update.effective_chat = chat

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.chat_id = chat_id
    msg.reply_text = AsyncMock()
    msg.reply_to_message = None
    msg.new_chat_members = []
    update.message = msg
    update.effective_message = msg
    return update


def test_addmember_command_by_user_id():
    """Verify /addmember <user_id> viewer adds the user to the current workspace."""
    async def _run():
        owner_id = 9001
        new_user_id = 777888
        chat_id = -1004310685141

        ws = get_or_create_workspace(title="Payment Group", creator_user_id=owner_id, chat_id=chat_id, chat_type="group")
        add_workspace_member(ws.id, owner_id, display_name="Owner", role="owner")

        update = make_update(owner_id, chat_id, f"/addmember {new_user_id} viewer")
        ctx = MagicMock()
        ctx.args = [str(new_user_id), "viewer"]
        ctx.bot = MagicMock()

        with patch("config.TELEGRAM_USER_ID", str(owner_id)):
            await addmember_command(update, ctx)

        assert update.message.reply_text.called
        reply = update.message.reply_text.call_args[0][0]
        assert "was added to this workspace with role <b>VIEWER</b>" in reply

        m = get_workspace_member(ws.id, new_user_id)
        assert m is not None
        assert m.role == "viewer"

    asyncio.run(_run())


def test_addmember_command_by_reply():
    """Verify /addmember by replying to a user's message adds them to the workspace."""
    async def _run():
        owner_id = 9001
        akhil_id = 1234567
        chat_id = -1004310685141

        ws = get_or_create_workspace(title="Payment Group", creator_user_id=owner_id, chat_id=chat_id, chat_type="group")
        add_workspace_member(ws.id, owner_id, display_name="Owner", role="owner")

        update = make_update(owner_id, chat_id, "/addmember viewer")
        reply_user = MagicMock(spec=User)
        reply_user.id = akhil_id
        reply_user.username = "akhil_user"
        reply_user.first_name = "Akhil"
        reply_user.full_name = "Akhil Sharma"

        reply_msg = MagicMock(spec=Message)
        reply_msg.from_user = reply_user
        update.message.reply_to_message = reply_msg

        ctx = MagicMock()
        ctx.args = ["viewer"]
        ctx.bot = MagicMock()

        with patch("config.TELEGRAM_USER_ID", str(owner_id)):
            await addmember_command(update, ctx)

        assert update.message.reply_text.called
        reply = update.message.reply_text.call_args[0][0]
        assert "was added to this workspace with role <b>VIEWER</b>" in reply

        m = get_workspace_member(ws.id, akhil_id)
        assert m is not None
        assert m.role == "viewer"
        assert m.display_name == "Akhil Sharma"

    asyncio.run(_run())


def test_auto_register_new_chat_members():
    """Verify handle_new_chat_members automatically registers new users added to the group in Telegram."""
    async def _run():
        owner_id = 9001
        akhil_id = 555666
        chat_id = -1004310685141

        ws = get_or_create_workspace(title="Payment Group", creator_user_id=owner_id, chat_id=chat_id, chat_type="group")
        add_workspace_member(ws.id, owner_id, display_name="Owner", role="owner")

        update = make_update(owner_id, chat_id)
        new_u = MagicMock(spec=User)
        new_u.id = akhil_id
        new_u.username = "akhil"
        new_u.first_name = "Akhil"
        new_u.full_name = "Akhil"

        update.message.new_chat_members = [new_u]
        update.effective_message.new_chat_members = [new_u]

        ctx = MagicMock()
        ctx.bot.id = 999999999

        await handle_new_chat_members(update, ctx)

        assert update.message.reply_text.called
        reply = update.message.reply_text.call_args[0][0]
        assert "Welcome" in reply
        assert "Akhil" in reply

        m = get_workspace_member(ws.id, akhil_id)
        assert m is not None
        assert m.role == "viewer"

    asyncio.run(_run())
