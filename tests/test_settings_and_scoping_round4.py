import asyncio
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from telegram import Update, User, Chat, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import setup_database, get_db_connection
from database.models import Transaction, Workspace
from database.queries import (
    get_or_create_workspace, add_workspace_member, get_workspace_by_id,
    get_workspace_by_chat_id, get_workspace_member,
    get_default_workspace_id, create_custom_workspace, set_workspace_setting,
    get_workspace_setting
)
from bot.auth import set_user_active_workspace, get_user_active_workspace
from services.transaction_service import commit_transaction, LimitExceededError
import config


SYNTHETIC_OWNER_ID = 8379948573
SYNTHETIC_MEMBER_ID = 555111222
SYNTHETIC_OTHER_USER_ID = 777333444


@pytest.fixture(autouse=True)
def clean_round4_db(tmp_path, monkeypatch):
    """Sets up a clean temporary SQLite database for each test in round 4."""
    db_file = tmp_path / "test_round4.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("database.db.DB_PATH", db_file)
    monkeypatch.setattr("services.backup_service.DB_PATH", db_file)
    monkeypatch.setattr("config.TELEGRAM_USER_ID", SYNTHETIC_OWNER_ID)
    monkeypatch.setattr("config.RESTRICTED_USER_IDS", set())
    setup_database()
    yield


def make_update(user_id: int, chat_id: int, chat_type: str = "private", text: str = ""):
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.full_name = f"User {user_id}"
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = 1001
    msg.chat_id = chat_id
    msg.chat = chat
    msg.from_user = user
    msg.reply_text = AsyncMock()
    msg.edit_text = AsyncMock()
    update.effective_user = user
    update.effective_chat = chat
    update.effective_message = msg
    update.message = msg
    update.callback_query = None
    return update


def make_callback_update(user_id: int, chat_id: int, data: str, chat_type: str = "private"):
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.full_name = f"User {user_id}"
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    query = MagicMock(spec=CallbackQuery)
    query.data = data
    query.from_user = user
    msg = MagicMock(spec=Message)
    msg.chat_id = chat_id
    msg.chat = chat
    msg.message_id = 2002
    msg.reply_text = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.edit_reply_markup = AsyncMock()
    query.message = msg
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update.effective_user = user
    update.effective_chat = chat
    update.effective_message = msg
    update.message = None
    update.callback_query = query
    return update


# ==============================================================================
# PART 1 TESTS — /workspaces Scoping & Hardened Switch
# ==============================================================================

def test_part1_member_sees_only_own_dm_workspace_in_workspaces_view():
    """Member opening /workspaces sees only their own workspace, 0 switch buttons to others."""
    from bot.commands import render_workspaces_view

    # Setup Owner workspace, Member DM workspace, and an Other workspace
    owner_ws = get_or_create_workspace(chat_id=SYNTHETIC_OWNER_ID, chat_type="private", title="Owner Ledger")
    member_ws = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Member Ledger")
    other_ws = get_or_create_workspace(chat_id=SYNTHETIC_OTHER_USER_ID, chat_type="private", title="Other Ledger")

    add_workspace_member(member_ws.id, SYNTHETIC_MEMBER_ID, role="member")
    add_workspace_member(other_ws.id, SYNTHETIC_MEMBER_ID, role="member")

    update = make_update(user_id=SYNTHETIC_MEMBER_ID, chat_id=SYNTHETIC_MEMBER_ID, chat_type="private")
    text, markup = render_workspaces_view(update)

    assert "Member Ledger" in text
    assert "Other Ledger" not in text
    assert "Owner Ledger" not in text
    assert "Group Ledgers:" not in text

    switch_callbacks = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("ws_switch:")
    ]
    assert len(switch_callbacks) == 0

    all_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "ws_new_prompt" not in all_callbacks
    assert "ws_reset" in all_callbacks


def test_part1_forged_ws_switch_callback_denied_for_non_owner():
    """Forged ws_switch callback to another workspace is refused and active workspace is unchanged."""
    async def _test():
        from bot.handlers import handle_callback_query

        member_ws = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Member Ledger")
        other_ws = get_or_create_workspace(chat_id=SYNTHETIC_OTHER_USER_ID, chat_type="private", title="Target Ledger")
        add_workspace_member(member_ws.id, SYNTHETIC_MEMBER_ID, role="member")
        add_workspace_member(other_ws.id, SYNTHETIC_MEMBER_ID, role="member")

        set_user_active_workspace(SYNTHETIC_MEMBER_ID, member_ws.id)

        update = make_callback_update(
            user_id=SYNTHETIC_MEMBER_ID,
            chat_id=SYNTHETIC_MEMBER_ID,
            data=f"ws_switch:{other_ws.id}",
            chat_type="private"
        )
        context = MagicMock()

        await handle_callback_query(update, context)

        update.callback_query.answer.assert_called()
        call_args = [str(arg) for arg in update.callback_query.answer.call_args[0]]
        assert any("not available to you" in a for a in call_args)
        assert get_user_active_workspace(SYNTHETIC_MEMBER_ID) == member_ws.id

    asyncio.run(_test())


def test_part1_direct_workspace_id_switch_denied_for_non_owner():
    """Direct /workspace <other_id> command from non-owner is denied."""
    async def _test():
        from bot.commands import workspace_command

        member_ws = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Member Ledger")
        other_ws = get_or_create_workspace(chat_id=SYNTHETIC_OTHER_USER_ID, chat_type="private", title="Other Ledger")
        add_workspace_member(member_ws.id, SYNTHETIC_MEMBER_ID, role="member")
        add_workspace_member(other_ws.id, SYNTHETIC_MEMBER_ID, role="member")

        set_user_active_workspace(SYNTHETIC_MEMBER_ID, member_ws.id)

        update = make_update(user_id=SYNTHETIC_MEMBER_ID, chat_id=SYNTHETIC_MEMBER_ID, chat_type="private")
        context = MagicMock()
        context.args = [other_ws.id]

        await workspace_command(update, context)

        update.message.reply_text.assert_called()
        reply = update.message.reply_text.call_args[0][0]
        assert "Access Denied" in reply or "not available" in reply
        assert get_user_active_workspace(SYNTHETIC_MEMBER_ID) == member_ws.id

    asyncio.run(_test())


def test_part1_owner_view_sees_all_and_can_switch():
    """Owner opening /workspaces sees all active workspaces and can switch."""
    async def _test():
        from bot.commands import render_workspaces_view, workspace_command

        ws1 = get_or_create_workspace(chat_id=SYNTHETIC_OWNER_ID, chat_type="private", title="Owner Ledger")
        ws2 = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Member Ledger")

        update = make_update(user_id=SYNTHETIC_OWNER_ID, chat_id=SYNTHETIC_OWNER_ID, chat_type="private")
        text, markup = render_workspaces_view(update)

        assert "Owner Ledger" in text
        assert "Member Ledger" in text
        switch_cbs = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith("ws_switch:")
        ]
        assert len(switch_cbs) >= 2

        ctx = MagicMock()
        ctx.args = [ws2.id]
        await workspace_command(update, ctx)
        assert get_user_active_workspace(SYNTHETIC_OWNER_ID) == ws2.id

    asyncio.run(_test())


# ==============================================================================
# PART 2 TESTS — /settings Panel: Permissions & Limits
# ==============================================================================

def test_part2_transaction_cap_enforcement_and_limit_error():
    """Per-transaction cap is enforced at record time naming the cap."""
    ws = get_or_create_workspace(chat_id=SYNTHETIC_OWNER_ID, chat_type="private", title="Owner Ledger")
    set_workspace_setting(ws.id, "per_tx_cap", "500")

    tx_ok = Transaction(amount=300.0, transaction_type="SENT", person_name="Coffee Shop", workspace_id=ws.id)
    assert commit_transaction(tx_ok) is True

    tx_over = Transaction(amount=750.0, transaction_type="SENT", person_name="Laptop Store", workspace_id=ws.id)
    with pytest.raises(LimitExceededError) as exc_info:
        commit_transaction(tx_over)
    assert "Limit reached" in str(exc_info.value)
    assert "500" in str(exc_info.value)


def test_part2_monthly_spending_cap_enforcement():
    """Monthly spending cap is enforced at record time naming the cap."""
    ws = get_or_create_workspace(chat_id=SYNTHETIC_OWNER_ID, chat_type="private", title="Owner Ledger")
    set_workspace_setting(ws.id, "monthly_spending_cap", "1000")

    tx1 = Transaction(amount=600.0, transaction_type="SENT", person_name="Groceries", workspace_id=ws.id)
    assert commit_transaction(tx1) is True

    tx_over = Transaction(amount=500.0, transaction_type="SENT", person_name="Dinner", workspace_id=ws.id)
    with pytest.raises(LimitExceededError) as exc_info:
        commit_transaction(tx_over)
    assert "Limit reached" in str(exc_info.value)
    assert "1,000" in str(exc_info.value) or "1000" in str(exc_info.value)


def test_part2_settings_non_owner_denied():
    """Non-owner running /settings is denied."""
    async def _test():
        from bot.commands import settings_command

        update = make_update(user_id=SYNTHETIC_MEMBER_ID, chat_id=SYNTHETIC_MEMBER_ID, chat_type="private")
        ctx = MagicMock()
        ctx.args = []

        await settings_command(update, ctx)
        update.message.reply_text.assert_called()
        call_str = str(update.message.reply_text.call_args)
        assert "Only the bot owner" in call_str or "Access Restricted" in call_str or "⛔" in call_str

    asyncio.run(_test())


def test_part2_settings_restrict_and_unrestrict_roundtrip():
    """Owner can restrict and unrestrict user IDs from chat."""
    async def _test():
        from bot.commands import settings_command
        from config import get_restricted_user_ids, is_restricted_user

        ws = get_or_create_workspace(chat_id=SYNTHETIC_OWNER_ID, chat_type="private", title="Owner Ledger")
        update = make_update(user_id=SYNTHETIC_OWNER_ID, chat_id=SYNTHETIC_OWNER_ID, chat_type="private")

        # Restrict user
        ctx_restrict = MagicMock()
        ctx_restrict.args = ["restrict", "987654321"]
        await settings_command(update, ctx_restrict)
        assert 987654321 in get_restricted_user_ids(ws.id)
        assert is_restricted_user(987654321, ws.id) is True

        # Unrestrict user
        ctx_unrestrict = MagicMock()
        ctx_unrestrict.args = ["unrestrict", "987654321"]
        await settings_command(update, ctx_unrestrict)
        assert 987654321 not in get_restricted_user_ids(ws.id)
        assert is_restricted_user(987654321, ws.id) is False

    asyncio.run(_test())


def test_part2_env_fallback_when_db_setting_absent(monkeypatch):
    """Config getters fall back to env variables when no DB setting is set."""
    monkeypatch.setenv("PER_TRANSACTION_CAP", "1500")
    monkeypatch.setenv("GEMINI_DAILY_QUOTA", "80")

    from config import get_per_transaction_cap, get_gemini_daily_quota
    ws = get_or_create_workspace(chat_id=SYNTHETIC_OWNER_ID, chat_type="private", title="Owner Ledger")

    assert get_per_transaction_cap(ws.id) == 1500.0
    assert get_gemini_daily_quota(ws.id) == 80


# ==============================================================================
# PART 3 TESTS — Simplified Access-Approval Screen (2 buttons)
# ==============================================================================

def test_part3_access_request_owner_notification_has_exactly_two_buttons():
    """Owner access request notification renders exactly 2 buttons: Approve and Reject."""
    async def _test():
        from bot.auth import require_authorized

        req_uid = 333444555
        update = make_update(user_id=req_uid, chat_id=req_uid, chat_type="private")

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        authorized = await require_authorized(update, context)
        assert authorized is False

        context.bot.send_message.assert_called()
        call_kwargs = context.bot.send_message.call_args[1]
        alert_text = call_kwargs['text']
        reply_markup = call_kwargs['reply_markup']

        assert "personal ledger" in alert_text.lower()

        buttons = [btn for row in reply_markup.inline_keyboard for btn in row]
        assert len(buttons) == 2
        cb_list = [btn.callback_data for btn in buttons]
        assert any(cb.startswith(f"auth_grant:{req_uid}") for cb in cb_list)
        assert any(cb.startswith(f"auth_deny:{req_uid}") for cb in cb_list)

    asyncio.run(_test())


def test_part3_auth_grant_assigns_base_member_in_dm_workspace():
    """Tapping auth_grant:{uid} activates user with base member role in their DM workspace."""
    async def _test():
        from bot.handlers import handle_callback_query

        req_uid = 444555666
        req_ws = get_or_create_workspace(chat_id=req_uid, chat_type="private", title=f"Personal Ledger {req_uid}")

        update = make_callback_update(
            user_id=SYNTHETIC_OWNER_ID,
            chat_id=SYNTHETIC_OWNER_ID,
            data=f"auth_grant:{req_uid}",
            chat_type="private"
        )
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await handle_callback_query(update, context)

        m = get_workspace_member(req_ws.id, req_uid)
        assert m is not None
        assert m.role == "member"
        assert bool(m.is_active) is True
        context.bot.send_message.assert_called()

    asyncio.run(_test())


def test_part3_backward_compatible_three_part_auth_grant_callback():
    """Old 3-part callback data auth_grant:{uid}:admin succeeds, ignores admin, grants base role member."""
    async def _test():
        from bot.handlers import handle_callback_query

        req_uid = 555666777
        req_ws = get_or_create_workspace(chat_id=req_uid, chat_type="private", title=f"Personal Ledger {req_uid}")

        update = make_callback_update(
            user_id=SYNTHETIC_OWNER_ID,
            chat_id=SYNTHETIC_OWNER_ID,
            data=f"auth_grant:{req_uid}:admin",
            chat_type="private"
        )
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await handle_callback_query(update, context)

        m = get_workspace_member(req_ws.id, req_uid)
        assert m is not None
        assert m.role == "member"

    asyncio.run(_test())


# ==============================================================================
# PART 4 TESTS — UI Polish (Ledger Indicator & Empty-State Actions)
# ==============================================================================

def test_part4_receipt_card_and_success_message_show_ledger_indicator():
    """format_receipt_card and format_success_message show ledger indicator when workspace_id is present."""
    from bot.handlers import format_receipt_card, format_success_message

    ws = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Kochi Vacation")
    tx = Transaction(
        id=42,
        amount=150.0,
        transaction_type="SENT",
        person_name="Coconut Stall",
        workspace_id=ws.id,
        balance_before=1000.0,
        balance_after=850.0
    )

    card = format_receipt_card(tx)
    assert "Ledger:" in card
    assert "Kochi Vacation" in card

    success = format_success_message(tx)
    assert "Ledger:" in success
    assert "Kochi Vacation" in success


def test_part4_empty_states_include_next_action_suggestion():
    """Empty state in render_history_page suggests sending first entry."""
    from bot.commands import render_history_page

    ws = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Fresh Ledger")
    text, _ = render_history_page(page=1, filter_type="ALL", workspace_id=ws.id)

    assert "No transactions found" in text
    assert "paid 200 for chai" in text


# ==============================================================================
# PART 5 TESTS — Export Gate for One-Person Model
# ==============================================================================

def test_part5_export_command_and_callback_gate():
    """Non-owner can export their own DM workspace, but is denied exporting others."""
    async def _test():
        from bot.commands import export_command
        from bot.handlers import handle_callback_query

        member_ws = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="Member Ledger")
        other_ws = get_or_create_workspace(chat_id=SYNTHETIC_OTHER_USER_ID, chat_type="private", title="Other Ledger")
        add_workspace_member(member_ws.id, SYNTHETIC_MEMBER_ID, role="member")
        add_workspace_member(other_ws.id, SYNTHETIC_MEMBER_ID, role="member")

        # 1. Non-owner exporting their own DM workspace: allowed (starts generation)
        set_user_active_workspace(SYNTHETIC_MEMBER_ID, member_ws.id)
        update_ok = make_update(user_id=SYNTHETIC_MEMBER_ID, chat_id=SYNTHETIC_MEMBER_ID, chat_type="private")
        ctx_ok = MagicMock()
        ctx_ok.args = ["excel"]

        with patch("bot.commands.send_excel_report", new_callable=AsyncMock) as mock_excel:
            await export_command(update_ok, ctx_ok)
            mock_excel.assert_called_once()

        # 2. Non-owner trying to export another workspace: denied
        set_user_active_workspace(SYNTHETIC_MEMBER_ID, other_ws.id)
        update_denied = make_update(user_id=SYNTHETIC_MEMBER_ID, chat_id=SYNTHETIC_MEMBER_ID, chat_type="private")
        ctx_denied = MagicMock()
        ctx_denied.args = ["excel"]

        with patch("bot.commands.send_excel_report", new_callable=AsyncMock) as mock_excel_denied:
            await export_command(update_denied, ctx_denied)
            mock_excel_denied.assert_not_called()
            reply_str = str(update_denied.message.reply_text.call_args)
            assert "Access Denied" in reply_str or "personal ledger" in reply_str

        # 3. Owner can export any workspace
        set_user_active_workspace(SYNTHETIC_OWNER_ID, other_ws.id)
        update_owner = make_update(user_id=SYNTHETIC_OWNER_ID, chat_id=SYNTHETIC_OWNER_ID, chat_type="private")
        ctx_owner = MagicMock()
        ctx_owner.args = ["excel"]

        with patch("bot.commands.send_excel_report", new_callable=AsyncMock) as mock_excel_owner:
            await export_command(update_owner, ctx_owner)
            mock_excel_owner.assert_called_once()

    asyncio.run(_test())


# ==============================================================================
# DATA NOTE TEST — Obsolete Members Reported Without Auto-Delete
# ==============================================================================

def test_data_note_repair_script_flags_foreign_members_without_deletion():
    """Repair script identifies non-owner members inside another user's workspace without deleting them."""
    from scripts.repair_workspace_provenance import repair_provenance

    # Workspace 1: belongs to user A (chat_id = SYNTHETIC_MEMBER_ID)
    ws_a = get_or_create_workspace(chat_id=SYNTHETIC_MEMBER_ID, chat_type="private", title="User A Ledger")
    # User A is in their own workspace
    add_workspace_member(ws_a.id, SYNTHETIC_MEMBER_ID, role="member")
    # User B (foreign non-owner) is placed in User A's workspace
    add_workspace_member(ws_a.id, SYNTHETIC_OTHER_USER_ID, role="member")

    # Run repair script with apply=True
    with get_db_connection() as conn:
        result = repair_provenance(conn, apply=True)

    assert result.get("success") is True
    obsolete = result.get("obsolete_members", {})
    assert obsolete.get("flagged") >= 1

    flagged_uids = [d["telegram_user_id"] for d in obsolete.get("details", [])]
    assert SYNTHETIC_OTHER_USER_ID in flagged_uids
    assert SYNTHETIC_MEMBER_ID not in flagged_uids

    # CRITICAL: Verify NOT auto-deleted from database
    m_check = get_workspace_member(ws_a.id, SYNTHETIC_OTHER_USER_ID)
    assert m_check is not None
