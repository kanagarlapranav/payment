import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
import datetime
import pytest

from bot.feature_registry import set_feature_permission
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    insert_transaction_with_balance,
)
from database.models import Transaction


def test_get_day_financial_summary_calculation():
    """Verify get_day_financial_summary accurately calculates opening, inflows, outflows, net change, and closing balance."""
    from services.balance_service import get_day_financial_summary, set_explicit_balance

    ws = get_or_create_workspace(
        title="Day Summary Calc Group",
        creator_user_id=888100,
        chat_id=-100888100,
        chat_type="group"
    )

    # Set initial base balance to 1000.00
    set_explicit_balance(1000.0, workspace_id=ws.id)

    # Day 1: 2026-11-01
    # SENT 200, RECEIVED 500
    t1 = Transaction(
        amount=200.0,
        transaction_type="SENT",
        person_name="Lunch Cafe",
        category="Food & Dining",
        transaction_date="2026-11-01",
        transaction_time="12:00:00",
        workspace_id=ws.id,
        telegram_user_id=888100
    )
    insert_transaction_with_balance(t1)

    t2 = Transaction(
        amount=500.0,
        transaction_type="RECEIVED",
        person_name="Client Advance",
        category="Income",
        transaction_date="2026-11-01",
        transaction_time="14:00:00",
        workspace_id=ws.id,
        telegram_user_id=888100
    )
    insert_transaction_with_balance(t2)

    # Day 1 summary check
    d1_summary = get_day_financial_summary(datetime.date(2026, 11, 1), workspace_id=ws.id)
    assert d1_summary["opening_balance"] == 1000.0
    assert d1_summary["inflows"] == 500.0
    assert d1_summary["outflows"] == 200.0
    assert d1_summary["net_change"] == 300.0
    assert d1_summary["closing_balance"] == 1300.0
    assert d1_summary["tx_count"] == 2
    assert d1_summary["inflow_count"] == 1
    assert d1_summary["outflow_count"] == 1

    # Day 2: 2026-11-02 (NO TRANSACTIONS)
    # Opening balance must equal closing balance of Day 1 (1300.0), net change = 0
    d2_summary = get_day_financial_summary(datetime.date(2026, 11, 2), workspace_id=ws.id)
    assert d2_summary["opening_balance"] == 1300.0
    assert d2_summary["inflows"] == 0.0
    assert d2_summary["outflows"] == 0.0
    assert d2_summary["net_change"] == 0.0
    assert d2_summary["closing_balance"] == 1300.0
    assert d2_summary["tx_count"] == 0

    # Day 3: 2026-11-03
    # SENT 300, TRANSFER 100
    t3 = Transaction(
        amount=300.0,
        transaction_type="SENT",
        person_name="Stationery Store",
        category="Supplies",
        transaction_date="2026-11-03",
        transaction_time="10:00:00",
        workspace_id=ws.id,
        telegram_user_id=888100
    )
    insert_transaction_with_balance(t3)

    t4 = Transaction(
        amount=100.0,
        transaction_type="TRANSFER",
        person_name="Self Savings",
        category="Transfer",
        transaction_date="2026-11-03",
        transaction_time="11:00:00",
        workspace_id=ws.id,
        telegram_user_id=888100
    )
    insert_transaction_with_balance(t4)

    # Day 3 summary check
    d3_summary = get_day_financial_summary(datetime.date(2026, 11, 3), workspace_id=ws.id)
    assert d3_summary["opening_balance"] == 1300.0
    assert d3_summary["inflows"] == 0.0
    assert d3_summary["outflows"] == 300.0
    assert d3_summary["transfers"] == 100.0
    assert d3_summary["net_change"] == -300.0
    assert d3_summary["closing_balance"] == 1000.0
    assert d3_summary["tx_count"] == 2
    assert d3_summary["top_category"] == ("Supplies", 300.0)


def test_day_command_flow_and_gating():
    """Verify /day command output formatting, argument parsing, feature gating, and scoping."""
    from bot.commands import day_command

    async def _run():
        group_chat_id = -100888200
        ws = get_or_create_workspace(
            title="Day Command Flow Group",
            creator_user_id=888200,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 888200
        member_uid = 888201
        other_uid = 888202

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        # Insert transactions on 2026-10-25
        t_member = Transaction(
            amount=450.0,
            transaction_type="SENT",
            person_name="Member Groceries",
            category="Food & Dining",
            transaction_date="2026-10-25",
            transaction_time="11:30:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t_member)

        t_other = Transaction(
            amount=1200.0,
            transaction_type="RECEIVED",
            person_name="Other Invoice",
            category="Income",
            transaction_date="2026-10-25",
            transaction_time="15:00:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid
        )
        insert_transaction_with_balance(t_other)

        # 1. Member with history revoked -> Denied
        set_feature_permission(ws.id, "history", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Day Command Flow Group"
        u_member.message.reply_text = AsyncMock()

        ctx_args = MagicMock()
        ctx_args.args = ["2026-10-25"]

        await day_command(u_member, ctx_args)
        assert u_member.message.reply_text.called
        denied_msg = u_member.message.reply_text.call_args[0][0]
        assert "Access Denied" in denied_msg or "restricted" in denied_msg

        # 2. Member with history allowed -> In group chat, member sees full day ledger
        set_feature_permission(ws.id, "history", "member", True)
        u_member.message.reply_text.reset_mock()

        await day_command(u_member, ctx_args)
        assert u_member.message.reply_text.called
        member_msg = u_member.message.reply_text.call_args[0][0]
        assert "Daily Financial Statement" in member_msg
        assert "25 Oct 2026" in member_msg
        # Member in group chat sees the full day ledger (Nov 8 decision: both 450 sent and 1,200 received)
        assert "450" in member_msg
        assert "1,200" in member_msg

        # 3. Owner -> Full workspace view
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Day Command Flow Group"
        u_owner.message.reply_text = AsyncMock()

        await day_command(u_owner, ctx_args)
        assert u_owner.message.reply_text.called
        owner_msg = u_owner.message.reply_text.call_args[0][0]
        assert "Daily Financial Statement" in owner_msg
        assert "25 Oct 2026" in owner_msg
        # Owner sees both inflows and outflows
        assert "1,200" in owner_msg
        assert "450" in owner_msg
        assert "Opening Balance" in owner_msg
        assert "Closing Balance" in owner_msg

        # 4. Invalid date string
        u_owner.message.reply_text.reset_mock()
        ctx_bad = MagicMock()
        ctx_bad.args = ["invalid-not-a-date"]
        await day_command(u_owner, ctx_bad)
        assert u_owner.message.reply_text.called
        bad_msg = u_owner.message.reply_text.call_args[0][0]
        assert "Could not parse date" in bad_msg

        # 5. Default with no args -> Today's date
        u_owner.message.reply_text.reset_mock()
        ctx_none = MagicMock()
        ctx_none.args = []
        await day_command(u_owner, ctx_none)
        assert u_owner.message.reply_text.called
        today_msg = u_owner.message.reply_text.call_args[0][0]
        assert "Daily Financial Statement" in today_msg

    asyncio.run(_run())


def test_day_command_registered_in_app():
    """Verify /day, /daily, /daysummary are registered in app handlers."""
    from app import build_application
    from telegram.ext import CommandHandler
    from unittest.mock import patch
    with patch("config.TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"):
        test_app = build_application()
    cmd_handlers = [h for group in test_app.handlers.values() for h in group if isinstance(h, CommandHandler)]
    registered_cmds = set()
    for h in cmd_handlers:
        registered_cmds.update(h.commands)
    assert "day" in registered_cmds
    assert "daily" in registered_cmds
    assert "daysummary" in registered_cmds


def test_handle_text_backslash_day_date():
    """Verify typing \\day 23/06/2026 routes cleanly via handle_text."""
    from bot.handlers import handle_text

    async def _run():
        group_chat_id = -100888300
        ws = get_or_create_workspace(
            title="Backslash Day Test Group",
            creator_user_id=888300,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 888300
        add_workspace_member(ws.id, owner_uid, role="owner")

        # Insert a transaction on 23/06/2026
        t = Transaction(
            amount=750.0,
            transaction_type="RECEIVED",
            person_name="Freelance Project",
            category="Income",
            transaction_date="2026-06-23",
            transaction_time="14:00:00",
            workspace_id=ws.id,
            telegram_user_id=owner_uid
        )
        insert_transaction_with_balance(t)

        u = MagicMock()
        u.effective_user.id = owner_uid
        u.effective_chat.id = group_chat_id
        u.effective_chat.type = "group"
        u.message.text = r"\day 23/06/2026"
        u.message.chat_id = group_chat_id
        u.message.message_id = 9999
        u.message.reply_text = AsyncMock()

        ctx = MagicMock()
        await handle_text(u, ctx)

        assert u.message.reply_text.called
        reply = u.message.reply_text.call_args[0][0]
        assert "Daily Financial Statement" in reply
        assert "23 Jun 2026" in reply
        assert "750" in reply

    asyncio.run(_run())


def test_day_command_scoping_in_dm_workspace_and_viewer_in_dm():
    """Verify that in personal DM workspaces, member and viewer see only their own rows."""
    from bot.commands import day_command

    async def _run():
        owner_uid = 888400
        member_uid = 888401
        viewer_uid = 888402

        # Personal DM workspace
        ws_dm = get_or_create_workspace(
            title="Personal DM Workspace",
            creator_user_id=owner_uid,
            chat_id=owner_uid,
            chat_type="dm"
        )
        add_workspace_member(ws_dm.id, owner_uid, role="owner")
        add_workspace_member(ws_dm.id, member_uid, role="member")
        add_workspace_member(ws_dm.id, viewer_uid, role="viewer")

        # Insert 2 transactions: one by member, one by owner
        t_member = Transaction(
            amount=300.0,
            transaction_type="SENT",
            person_name="Member Snack",
            category="Food & Dining",
            transaction_date="2026-10-26",
            transaction_time="12:00:00",
            workspace_id=ws_dm.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t_member)

        t_owner = Transaction(
            amount=5000.0,
            transaction_type="RECEIVED",
            person_name="Salary",
            category="Income",
            transaction_date="2026-10-26",
            transaction_time="10:00:00",
            workspace_id=ws_dm.id,
            telegram_user_id=owner_uid
        )
        insert_transaction_with_balance(t_owner)

        ctx_args = MagicMock()
        ctx_args.args = ["2026-10-26"]

        from bot.auth import set_user_active_workspace
        set_user_active_workspace(member_uid, ws_dm.id)
        set_user_active_workspace(viewer_uid, ws_dm.id)

        # 1. Member in DM workspace -> Only sees own 300, NOT owner's 5000
        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_chat.id = member_uid
        u_member.effective_chat.type = "private"
        u_member.message.reply_text = AsyncMock()

        await day_command(u_member, ctx_args)
        assert u_member.message.reply_text.called
        m_msg = u_member.message.reply_text.call_args[0][0]
        assert "300" in m_msg
        assert "5,000" not in m_msg

        # 2. Viewer in DM workspace -> Only sees own rows (0 txs), NOT owner's 5000
        u_viewer = MagicMock()
        u_viewer.effective_user.id = viewer_uid
        u_viewer.effective_chat.id = viewer_uid
        u_viewer.effective_chat.type = "private"
        u_viewer.message.reply_text = AsyncMock()

        await day_command(u_viewer, ctx_args)
        assert u_viewer.message.reply_text.called
        v_msg = u_viewer.message.reply_text.call_args[0][0]
        assert "5,000" not in v_msg

    asyncio.run(_run())

