import pytest
from unittest.mock import MagicMock, patch
from telegram import Update, User, Chat, Message
from database.db import setup_database, get_db_connection
from database.models import Transaction
from database.queries import (
    get_or_create_workspace,
    get_default_workspace_id,
    insert_transaction,
    get_transactions_paginated,
    search_transactions
)
from services.balance_service import (
    recalculate_all_balances,
    get_today_summary,
    get_overall_summary
)
from bot.auth import get_workspace_context
from bot.commands import render_history_page, render_home_menu_text


@pytest.fixture(autouse=True)
def init_db():
    setup_database()


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "private", text: str = ""):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"Name_{user_id}"
    update.effective_user = user
    
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    chat.title = f"Chat_{chat_id}"
    update.effective_chat = chat

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = user
    msg.chat = chat
    update.message = msg
    update.effective_message = msg
    return update


def test_private_dm_routing_isolation():
    """Verifies that non-owner users in private chats get their own isolated workspace, not the group ledger."""
    owner_id = 8379948573
    friend_id = 8343764796
    default_ws_id = get_default_workspace_id()

    # Add friend as member to the group workspace
    from database.queries import add_workspace_member
    add_workspace_member(default_ws_id, friend_id, role="member")

    with patch('config.TELEGRAM_USER_ID', str(owner_id)):
        # 1. Global owner in private chat routes to default workspace
        update_owner = make_mock_update(user_id=owner_id, chat_id=owner_id, chat_type="private")
        ctx_owner = get_workspace_context(update_owner)
        assert ctx_owner is not None
        assert ctx_owner.workspace_id == default_ws_id

        # 2. Friend / non-owner in private chat routes to their OWN personal workspace
        update_friend = make_mock_update(user_id=friend_id, chat_id=friend_id, chat_type="private")
        ctx_friend = get_workspace_context(update_friend)
        assert ctx_friend is not None
        assert ctx_friend.workspace_id != default_ws_id
        assert ctx_friend.chat_type in ("private", "dm")
        assert ctx_friend.workspace.chat_type == "dm"


def test_per_user_scoping_in_shared_workspace():
    """Verifies that transactions and summaries in a shared workspace are cleanly scoped per user."""
    owner_id = 8379948573
    friend_id = 8343764796
    ws = get_or_create_workspace(chat_id=-100999111222, chat_type="group", title="Shared Group Test")

    with patch('config.TELEGRAM_USER_ID', str(owner_id)):
        # Insert transaction for Owner: ₹500 Sent
        tx_owner = Transaction(
            amount=500.0,
            transaction_type="SENT",
            person_name="Owner Grocery",
            category="Food & Dining",
            transaction_date="2026-10-09",
            workspace_id=ws.id,
            telegram_user_id=owner_id
        )
        insert_transaction(tx_owner)

        # Insert transaction for Friend: ₹120 Sent
        tx_friend = Transaction(
            amount=120.0,
            transaction_type="SENT",
            person_name="Friend Dosa",
            category="Food & Dining",
            transaction_date="2026-10-09",
            workspace_id=ws.id,
            telegram_user_id=friend_id
        )
        insert_transaction(tx_friend)

        # 1. get_transactions_paginated per user
        data_owner = get_transactions_paginated(workspace_id=ws.id, user_id=owner_id)
        assert data_owner['total_count'] == 1
        assert data_owner['transactions'][0]['person_name'] == "Owner Grocery"

        data_friend = get_transactions_paginated(workspace_id=ws.id, user_id=friend_id)
        assert data_friend['total_count'] == 1
        assert data_friend['transactions'][0]['person_name'] == "Friend Dosa"

        # 2. get_today_summary per user
        today_owner = get_today_summary(workspace_id=ws.id, user_id=owner_id)
        assert today_owner.transaction_count == 1
        assert today_owner.total_sent == 500.0

        today_friend = get_today_summary(workspace_id=ws.id, user_id=friend_id)
        assert today_friend.transaction_count == 1
        assert today_friend.total_sent == 120.0

        # 3. get_overall_summary per user
        overall_owner = get_overall_summary(workspace_id=ws.id, user_id=owner_id)
        assert overall_owner.transaction_count == 1
        assert overall_owner.total_sent == 500.0

        overall_friend = get_overall_summary(workspace_id=ws.id, user_id=friend_id)
        assert overall_friend.transaction_count == 1
        assert overall_friend.total_sent == 120.0
        assert overall_friend.current_balance == -120.0

        # 4. render_history_page per user
        text_owner, _ = render_history_page(workspace_id=ws.id, user_id=owner_id)
        assert "Owner Grocery" in text_owner
        assert "Friend Dosa" not in text_owner

        text_friend, _ = render_history_page(workspace_id=ws.id, user_id=friend_id)
        assert "Friend Dosa" in text_friend
        assert "Owner Grocery" not in text_friend

        # 5. search_transactions per user
        search_owner = search_transactions(query_text="Dosa", workspace_id=ws.id, user_id=owner_id)
        assert len(search_owner) == 0

        search_friend = search_transactions(query_text="Dosa", workspace_id=ws.id, user_id=friend_id)
        assert len(search_friend) == 1
        assert search_friend[0]['person_name'] == "Friend Dosa"


def test_nagendra_balance_starts_from_zero_and_calculates_negative():
    """Verifies that friend Nagendra balance starts from 0 and calculates negative for expenses."""
    owner_id = 8379948573
    friend_id = 8343764796
    
    # Create Nagendra personal workspace
    ws_nagendra = get_or_create_workspace(chat_id=friend_id, chat_type="dm", title="Nagendra (Personal)", creator_user_id=friend_id)
    
    # 1. Set initial balance for Nagendra workspace to 0.0
    from database.queries import set_workspace_setting
    set_workspace_setting(ws_nagendra.id, "initial_balance", "0.0")

    # 2. Insert Nagendra's 2 expenses: ₹20 and ₹120
    tx1 = Transaction(
        amount=20.0,
        transaction_type="SENT",
        person_name="K Vikraman Nair",
        category="Food & Dining",
        transaction_date="2026-10-06",
        occurred_at="2026-10-06 18:03:00",
        workspace_id=ws_nagendra.id,
        telegram_user_id=friend_id
    )
    insert_transaction(tx1)

    tx2 = Transaction(
        amount=120.0,
        transaction_type="SENT",
        person_name="Dosa",
        category="Food & Dining",
        transaction_date="2026-10-07",
        occurred_at="2026-10-07 22:23:00",
        workspace_id=ws_nagendra.id,
        telegram_user_id=friend_id
    )
    insert_transaction(tx2)

    # 3. Recalculate balances
    final_bal = recalculate_all_balances(workspace_id=ws_nagendra.id)
    assert final_bal == -140.0

    # 4. Overall summary in Nagendra workspace
    summary = get_overall_summary(workspace_id=ws_nagendra.id, user_id=friend_id)
    assert summary.current_balance == -140.0
    assert summary.total_sent == 140.0
    assert summary.total_received == 0.0
    assert summary.net_change == -140.0
    assert summary.transaction_count == 2

    # 5. Format currency handles negative
    from utils.currency import format_currency
    assert format_currency(summary.current_balance) == "-₹140"

    # 6. Verify set_explicit_balance allows negative balance
    from services.balance_service import set_explicit_balance
    new_bal = set_explicit_balance(-200.0, workspace_id=ws_nagendra.id)
    assert new_bal == -200.0
    # Reset back to -140.0
    set_explicit_balance(-140.0, workspace_id=ws_nagendra.id)

