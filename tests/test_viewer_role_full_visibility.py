import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from telegram import Update, User, Chat, Message

from database.db import setup_database
from database.models import Transaction
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    insert_transaction,
    get_workspace_member,
    can_user_modify_transaction
)
from services.balance_service import get_overall_summary, get_today_summary
from bot.auth import get_workspace_context, set_user_active_workspace
from bot.commands import (
    last5_command,
    balance_command,
    day_command,
    history_command,
    render_workspaces_view
)
from bot.feature_registry import set_feature_permission


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "group", chat_title: str = "Payment Group"):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"Name_{user_id}"
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
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


@pytest.fixture(autouse=True)
def setup_db():
    setup_database()


def test_viewer_role_sees_full_ledger_in_group_workspace():
    """Verifies that a user with 'viewer' role sees all workspace records and balance in a group workspace."""
    async def _test():
        owner_id = 9001
        viewer_id = 9002
        group_chat_id = -100777123

        ws = get_or_create_workspace(
            title="Payment Group",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, viewer_id, role="viewer")

        # Insert 2 transactions created by the owner
        from utils.dates import get_current_time_in_tz
        today_str = str(get_current_time_in_tz().date())

        t1 = Transaction(
            amount=500.0,
            transaction_type="RECEIVED",
            person_name="Customer Alice",
            category="Sales",
            transaction_date=today_str,
            workspace_id=ws.id,
            telegram_user_id=owner_id
        )
        insert_transaction(t1)

        t2 = Transaction(
            amount=150.0,
            transaction_type="SENT",
            person_name="Supplier Bob",
            category="Supplies",
            transaction_date=today_str,
            workspace_id=ws.id,
            telegram_user_id=owner_id
        )
        insert_transaction(t2)

        # 1. Viewer in group chat runs /last5
        u_viewer_grp = make_mock_update(user_id=viewer_id, chat_id=group_chat_id, chat_type="group")
        ctx = MagicMock()
        ctx.args = []

        await last5_command(u_viewer_grp, ctx)
        assert u_viewer_grp.message.reply_text.called
        reply = u_viewer_grp.message.reply_text.call_args[0][0]
        # Should see Customer Alice and Supplier Bob, NOT "No recent transactions found"
        assert "Customer Alice" in reply
        assert "Supplier Bob" in reply

        # 2. Viewer runs /balance
        u_viewer_grp.message.reply_text.reset_mock()
        await balance_command(u_viewer_grp, ctx)
        assert u_viewer_grp.message.reply_text.called
        bal_reply = u_viewer_grp.message.reply_text.call_args[0][0]
        assert "350" in bal_reply  # 500 - 150 = 350 balance

        # 3. Viewer runs /day
        u_viewer_grp.message.reply_text.reset_mock()
        await day_command(u_viewer_grp, ctx)
        assert u_viewer_grp.message.reply_text.called
        day_reply = u_viewer_grp.message.reply_text.call_args[0][0]
        assert "2 transactions" in day_reply
        assert "350" in day_reply

        # 4. Viewer cannot modify/edit transactions
        assert can_user_modify_transaction(t1.id, viewer_id, user_role="viewer", workspace_id=ws.id) is False

        # 5. Viewer in DM sees only their DM workspace in /workspaces, and no switch button for Payment Group (reverted)
        u_viewer_dm = make_mock_update(user_id=viewer_id, chat_id=viewer_id, chat_type="private", chat_title="Direct Messages")
        text, markup = render_workspaces_view(u_viewer_dm)
        assert "Payment Group" not in text
        # Verify there is NO switch button for Payment Group
        found_btn = False
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == f"ws_switch:{ws.id}":
                    found_btn = True
        assert found_btn is False

    asyncio.run(_test())


def test_viewer_role_sees_own_rows_only_in_personal_dm_workspace():
    """Verifies that a user with 'viewer' role in a personal DM workspace sees only their own rows (caller_id)."""
    async def _test():
        owner_id = 9051
        viewer_id = 9052

        ws_dm = get_or_create_workspace(
            title=f"User {owner_id}",
            creator_user_id=owner_id,
            chat_id=owner_id,
            chat_type="dm"
        )
        add_workspace_member(ws_dm.id, owner_id, role="owner")
        add_workspace_member(ws_dm.id, viewer_id, role="viewer")

        from utils.dates import get_current_time_in_tz
        today_str = str(get_current_time_in_tz().date())

        t_owner = Transaction(
            amount=800.0,
            transaction_type="RECEIVED",
            person_name="Owner Secret Client",
            category="Income",
            transaction_date=today_str,
            workspace_id=ws_dm.id,
            telegram_user_id=owner_id
        )
        insert_transaction(t_owner)

        # Viewer in DM runs /last5
        u_viewer_dm = make_mock_update(user_id=viewer_id, chat_id=viewer_id, chat_type="private")
        ctx = MagicMock()
        ctx.args = []

        await last5_command(u_viewer_dm, ctx)
        assert u_viewer_dm.message.reply_text.called
        reply = u_viewer_dm.message.reply_text.call_args[0][0]
        # Viewer must NOT see Owner Secret Client
        assert "Owner Secret Client" not in reply

    asyncio.run(_test())



def test_member_sees_full_transactions_in_group_chat():
    """Verifies that a user with 'member' role in a group chat sees all workspace records and balance."""
    async def _test():
        owner_id = 9101
        member_id = 9102
        group_chat_id = -100777999

        ws = get_or_create_workspace(
            title="Payment",
            creator_user_id=owner_id,
            chat_id=group_chat_id,
            chat_type="group"
        )
        add_workspace_member(ws.id, owner_id, role="owner")
        add_workspace_member(ws.id, member_id, role="member")

        from utils.dates import get_current_time_in_tz
        today_str = str(get_current_time_in_tz().date())

        t1 = Transaction(
            amount=1200.0,
            transaction_type="RECEIVED",
            person_name="Praneeth",
            category="Transfers & P2P",
            transaction_date=today_str,
            workspace_id=ws.id,
            telegram_user_id=owner_id
        )
        insert_transaction(t1)

        t2 = Transaction(
            amount=400.0,
            transaction_type="SENT",
            person_name="Grocery Store",
            category="Food & Dining",
            transaction_date=today_str,
            workspace_id=ws.id,
            telegram_user_id=owner_id
        )
        insert_transaction(t2)

        # Member runs /last5 in group chat
        u_member_grp = make_mock_update(user_id=member_id, chat_id=group_chat_id, chat_type="group", chat_title="Payment")
        ctx = MagicMock()
        ctx.args = []

        await last5_command(u_member_grp, ctx)
        assert u_member_grp.message.reply_text.called
        reply = u_member_grp.message.reply_text.call_args[0][0]
        # Member sees the group transactions!
        assert "Praneeth" in reply
        assert "Grocery Store" in reply

        # Member runs /balance in group chat
        u_member_grp.message.reply_text.reset_mock()
        await balance_command(u_member_grp, ctx)
        assert u_member_grp.message.reply_text.called
        bal_reply = u_member_grp.message.reply_text.call_args[0][0]
        assert "800" in bal_reply  # 1200 - 400 = 800

        # Member runs /day in group chat
        u_member_grp.message.reply_text.reset_mock()
        await day_command(u_member_grp, ctx)
        assert u_member_grp.message.reply_text.called
        day_reply = u_member_grp.message.reply_text.call_args[0][0]
        assert "2 transactions" in day_reply
        assert "800" in day_reply

    asyncio.run(_test())
