import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery

import config
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance,
    get_transaction_by_id,
    can_user_modify_transaction,
    get_user_recent_transactions,
    get_or_create_workspace,
    add_workspace_member,
    update_workspace_member_role,
    get_workspace_member
)
import bot.commands as cmd_module
from bot.commands import edit_command, delete_command, undo_command
from bot.handlers import handle_callback_query

OWNER_USER_ID = 11111111
ADMIN_USER_ID = 22222222
MEMBER_A_ID = 33333333
MEMBER_B_ID = 44444444
GROUP_CHAT_ID = -10099887766

@pytest.fixture(autouse=True)
def setup_test_workspace(tmp_path, monkeypatch):
    db_file = tmp_path / "test_self_service.sqlite3"
    monkeypatch.setattr(config, "DB_PATH", db_file)
    monkeypatch.setattr("database.db.DB_PATH", db_file)
    monkeypatch.setattr("services.backup_service.DB_PATH", db_file)
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER_USER_ID)
    monkeypatch.setattr(config, "TELEGRAM_GROUP_ID", GROUP_CHAT_ID)

    from database.db import setup_database
    setup_database()

    ws = get_or_create_workspace(GROUP_CHAT_ID, "supergroup", "Test Group", creator_user_id=OWNER_USER_ID)
    add_workspace_member(ws['id'], ADMIN_USER_ID, username="admin_user", display_name="Admin User", role="admin")
    add_workspace_member(ws['id'], MEMBER_A_ID, username="member_a", display_name="Member A", role="member")
    add_workspace_member(ws['id'], MEMBER_B_ID, username="member_b", display_name="Member B", role="member")
    return ws

def make_update(user_id, chat_id=GROUP_CHAT_ID, text="", callback_data=None):
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"User {user_id}"
    update.effective_user = user

    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = "supergroup" if chat_id < 0 else "private"
    chat.title = "Test Group"
    update.effective_chat = chat

    if callback_data is not None:
        cq = MagicMock(spec=CallbackQuery)
        cq.data = callback_data
        cq.from_user = user
        cq.answer = AsyncMock()
        cq.edit_message_text = AsyncMock()
        update.callback_query = cq
        update.effective_message = None
        update.message = None
    else:
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.chat_id = chat_id
        msg.message_id = 100
        msg.from_user = user
        msg.reply_text = AsyncMock()
        update.effective_message = msg
        update.message = msg
        update.callback_query = None

    return update

def test_member_and_admin_modification_permissions(setup_test_workspace):
    ws = setup_test_workspace

    # 1. Member A creates transaction
    tx_a = Transaction(
        amount=500.0,
        transaction_type="SENT",
        person_name="Vendor A",
        workspace_id=ws['id'],
        telegram_user_id=MEMBER_A_ID
    )
    tx_a_id = insert_transaction_with_balance(tx_a)

    # 2. Member B creates transaction
    tx_b = Transaction(
        amount=300.0,
        transaction_type="SENT",
        person_name="Vendor B",
        workspace_id=ws['id'],
        telegram_user_id=MEMBER_B_ID
    )
    tx_b_id = insert_transaction_with_balance(tx_b)

    # Member A can modify their own transaction
    assert can_user_modify_transaction(tx_a_id, MEMBER_A_ID, "member") is True
    # Member A CANNOT modify Member B's transaction
    assert can_user_modify_transaction(tx_b_id, MEMBER_A_ID, "member") is False

    # Member B can modify their own transaction
    assert can_user_modify_transaction(tx_b_id, MEMBER_B_ID, "member") is True
    # Member B CANNOT modify Member A's transaction
    assert can_user_modify_transaction(tx_a_id, MEMBER_B_ID, "member") is False

    # Admin can modify BOTH
    assert can_user_modify_transaction(tx_a_id, ADMIN_USER_ID, "admin") is True
    assert can_user_modify_transaction(tx_b_id, ADMIN_USER_ID, "admin") is True

    # Owner can modify BOTH
    assert can_user_modify_transaction(tx_a_id, OWNER_USER_ID, "owner") is True
    assert can_user_modify_transaction(tx_b_id, OWNER_USER_ID, "owner") is True

def test_member_edit_command_restricts_to_own_payments(setup_test_workspace):
    ws = setup_test_workspace

    # Member A creates a payment
    tx_a = Transaction(amount=250.0, transaction_type="SENT", person_name="Coffee", workspace_id=ws['id'], telegram_user_id=MEMBER_A_ID)
    tx_a_id = insert_transaction_with_balance(tx_a)

    # Member B creates a payment
    tx_b = Transaction(amount=750.0, transaction_type="SENT", person_name="Dinner", workspace_id=ws['id'], telegram_user_id=MEMBER_B_ID)
    tx_b_id = insert_transaction_with_balance(tx_b)

    # Member A tries to edit Member B's payment -> rejected
    up_a_on_b = make_update(MEMBER_A_ID, text=f"/edit {tx_b_id}")
    ctx = MagicMock()
    ctx.args = [str(tx_b_id)]
    asyncio.run(edit_command(up_a_on_b, ctx))
    call_text = str(up_a_on_b.effective_message.reply_text.call_args)
    assert "Permission Denied" in call_text or "only edit payments that you recorded" in call_text

    # Member A edits their own payment -> allowed
    up_a_on_a = make_update(MEMBER_A_ID, text=f"/edit {tx_a_id}")
    ctx = MagicMock()
    ctx.args = [str(tx_a_id)]
    asyncio.run(edit_command(up_a_on_a, ctx))
    call_text = str(up_a_on_a.effective_message.reply_text.call_args)
    assert "Editing Transaction" in call_text

    # Admin edits Member A's payment -> allowed
    up_admin_on_a = make_update(ADMIN_USER_ID, text=f"/edit {tx_a_id}")
    ctx = MagicMock()
    ctx.args = [str(tx_a_id)]
    asyncio.run(edit_command(up_admin_on_a, ctx))
    call_text = str(up_admin_on_a.effective_message.reply_text.call_args)
    assert "Editing Transaction" in call_text

def test_member_delete_command_restricts_to_own_payments(setup_test_workspace):
    ws = setup_test_workspace

    tx_a = Transaction(amount=120.0, transaction_type="SENT", person_name="Snacks", workspace_id=ws['id'], telegram_user_id=MEMBER_A_ID)
    tx_a_id = insert_transaction_with_balance(tx_a)

    tx_b = Transaction(amount=800.0, transaction_type="SENT", person_name="Groceries", workspace_id=ws['id'], telegram_user_id=MEMBER_B_ID)
    tx_b_id = insert_transaction_with_balance(tx_b)

    # Member A tries to delete Member B's payment -> rejected
    up_a_on_b = make_update(MEMBER_A_ID, text=f"/delete {tx_b_id}")
    ctx = MagicMock()
    ctx.args = [str(tx_b_id)]
    asyncio.run(delete_command(up_a_on_b, ctx))
    call_text = str(up_a_on_b.effective_message.reply_text.call_args)
    assert "Permission Denied" in call_text or "only delete payments that you recorded" in call_text

    # Member A deletes their own payment -> allowed
    up_a_on_a = make_update(MEMBER_A_ID, text=f"/delete {tx_a_id}")
    ctx = MagicMock()
    ctx.args = [str(tx_a_id)]
    asyncio.run(delete_command(up_a_on_a, ctx))
    call_text = str(up_a_on_a.effective_message.reply_text.call_args)
    assert "Delete Transaction" in call_text

    # Admin deletes Member A's payment -> allowed
    up_admin_on_a = make_update(ADMIN_USER_ID, text=f"/delete {tx_a_id}")
    ctx = MagicMock()
    ctx.args = [str(tx_a_id)]
    asyncio.run(delete_command(up_admin_on_a, ctx))
    call_text = str(up_admin_on_a.effective_message.reply_text.call_args)
    assert "Delete Transaction" in call_text
