import asyncio
from unittest.mock import MagicMock, AsyncMock
import pytest
from bot.feature_registry import set_feature_permission
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    insert_transaction_with_balance,
)
from database.models import Transaction


def test_fix1_last5_command_gate_and_scope():
    """Fix 1: last5_command must be gated on 'history' and scoped to caller's own rows for members."""
    from bot.commands import last5_command

    async def _run():
        group_chat_id = -100888111
        ws = get_or_create_workspace(
            title="Fix 1 Test Group",
            creator_user_id=111000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 111000
        member_uid = 111001
        other_uid = 111002

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        # Insert transactions for member and other
        t_member = Transaction(
            amount=50.0,
            transaction_type="SENT",
            person_name="Member Chai",
            category="Food & Dining",
            transaction_date="2026-10-15",
            transaction_time="10:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t_member)

        t_other = Transaction(
            amount=150.0,
            transaction_type="SENT",
            person_name="Other Secret Item",
            category="Shopping",
            transaction_date="2026-10-15",
            transaction_time="10:05:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid
        )
        insert_transaction_with_balance(t_other)

        # 1. Member with history revoked -> Denied
        set_feature_permission(ws.id, "history", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member1"
        u_member.effective_user.first_name = "Member1"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 1 Test Group"
        u_member.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = []

        await last5_command(u_member, ctx)

        assert u_member.message.reply_text.called
        call_args = u_member.message.reply_text.call_args[0][0]
        assert "⛔ <b>Access Denied:</b> History feature is restricted for your role in this workspace." in call_args

        # 2. Member with history allowed -> Scoped to own transactions only
        set_feature_permission(ws.id, "history", "member", True)
        u_member.message.reply_text.reset_mock()

        await last5_command(u_member, ctx)
        assert u_member.message.reply_text.called
        text_reply = u_member.message.reply_text.call_args[0][0]
        assert "Member Chai" in text_reply
        assert "Other Secret Item" not in text_reply

        # 3. Owner path -> Sees all transactions
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 1 Test Group"
        u_owner.message.reply_text = AsyncMock()

        await last5_command(u_owner, ctx)
        assert u_owner.message.reply_text.called
        owner_text = u_owner.message.reply_text.call_args[0][0]
        assert "Member Chai" in owner_text
        assert "Other Secret Item" in owner_text

    asyncio.run(_run())


def test_fix2_sort_command_gate_and_scope():
    """Fix 2: sort_command must be gated on 'history' and scoped to caller's own rows for members."""
    from bot.commands import sort_command

    async def _run():
        group_chat_id = -100888222
        ws = get_or_create_workspace(
            title="Fix 2 Test Group",
            creator_user_id=222000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 222000
        member_uid = 222001
        other_uid = 222002

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        # Insert transactions for member and other
        t_member = Transaction(
            amount=50.0,
            transaction_type="SENT",
            person_name="Member Coffee",
            category="Food & Dining",
            transaction_date="2026-10-15",
            transaction_time="11:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t_member)

        t_other = Transaction(
            amount=250.0,
            transaction_type="SENT",
            person_name="Other Secret Dinner",
            category="Food & Dining",
            transaction_date="2026-10-15",
            transaction_time="11:05:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid
        )
        insert_transaction_with_balance(t_other)

        # 1. Member with history revoked -> Denied on /sort with or without args
        set_feature_permission(ws.id, "history", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member2"
        u_member.effective_user.first_name = "Member2"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 2 Test Group"
        u_member.message.reply_text = AsyncMock()

        ctx_args = MagicMock()
        ctx_args.args = ["high"]

        await sort_command(u_member, ctx_args)
        assert u_member.message.reply_text.called
        call_args = u_member.message.reply_text.call_args[0][0]
        assert "⛔ <b>Access Denied:</b> History feature is restricted for your role in this workspace." in call_args

        # Also test /sort without args
        u_member.message.reply_text.reset_mock()
        ctx_no_args = MagicMock()
        ctx_no_args.args = []
        await sort_command(u_member, ctx_no_args)
        assert u_member.message.reply_text.called
        call_no_args = u_member.message.reply_text.call_args[0][0]
        assert "⛔ <b>Access Denied:</b> History feature is restricted for your role in this workspace." in call_no_args

        # 2. Member with history allowed -> Scoped to own rows
        set_feature_permission(ws.id, "history", "member", True)
        u_member.message.reply_text.reset_mock()

        await sort_command(u_member, ctx_args)
        assert u_member.message.reply_text.called
        text_reply = u_member.message.reply_text.call_args[0][0]
        assert "Member Coffee" in text_reply
        assert "Other Secret Dinner" not in text_reply

        # 3. Owner path -> Sees all rows
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 2 Test Group"
        u_owner.message.reply_text = AsyncMock()

        await sort_command(u_owner, ctx_args)
        assert u_owner.message.reply_text.called
        owner_text = u_owner.message.reply_text.call_args[0][0]
        assert "Member Coffee" in owner_text
        assert "Other Secret Dinner" in owner_text

    asyncio.run(_run())


def test_fix3_sort_callback_gate_and_scope():
    """Fix 3: sort: callback branch must be gated on 'history' and scoped to caller's own rows for members."""
    from bot.handlers import _dispatch_callback_query

    async def _run():
        group_chat_id = -100888333
        ws = get_or_create_workspace(
            title="Fix 3 Test Group",
            creator_user_id=333000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 333000
        member_uid = 333001
        other_uid = 333002

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        # Insert transactions
        t_member = Transaction(
            amount=60.0,
            transaction_type="SENT",
            person_name="Member Snack",
            category="Food & Dining",
            transaction_date="2026-10-15",
            transaction_time="12:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t_member)

        t_other = Transaction(
            amount=600.0,
            transaction_type="SENT",
            person_name="Other Luxury Watch",
            category="Shopping",
            transaction_date="2026-10-15",
            transaction_time="12:05:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid
        )
        insert_transaction_with_balance(t_other)

        # 1. Member with history revoked -> Denied
        set_feature_permission(ws.id, "history", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member3"
        u_member.effective_user.first_name = "Member3"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 3 Test Group"

        query_member = MagicMock()
        query_member.data = "sort:amount_desc"
        query_member.from_user.id = member_uid
        query_member.answer = AsyncMock()
        query_member.edit_message_text = AsyncMock()
        query_member.message.chat.id = group_chat_id
        u_member.callback_query = query_member

        ctx = MagicMock()

        await _dispatch_callback_query(u_member, ctx)

        # Must answer alert and NOT edit message text
        query_member.answer.assert_any_call("⛔ History is restricted for your role in this workspace.", show_alert=True)
        query_member.edit_message_text.assert_not_called()

        # 2. Member with history allowed -> Scoped to own transactions only
        set_feature_permission(ws.id, "history", "member", True)
        query_member.answer.reset_mock()
        query_member.edit_message_text.reset_mock()

        await _dispatch_callback_query(u_member, ctx)
        assert query_member.edit_message_text.called
        member_text = query_member.edit_message_text.call_args[0][0]
        assert "Member Snack" in member_text
        assert "Other Luxury Watch" not in member_text

        # 3. Owner path -> Sees all transactions
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 3 Test Group"

        query_owner = MagicMock()
        query_owner.data = "sort:amount_desc"
        query_owner.from_user.id = owner_uid
        query_owner.answer = AsyncMock()
        query_owner.edit_message_text = AsyncMock()
        query_owner.message.chat.id = group_chat_id
        u_owner.callback_query = query_owner

        await _dispatch_callback_query(u_owner, ctx)
        assert query_owner.edit_message_text.called
        owner_text = query_owner.edit_message_text.call_args[0][0]
        assert "Member Snack" in owner_text
        assert "Other Luxury Watch" in owner_text

    asyncio.run(_run())


def test_fix4_details_command_scope():
    """Fix 4: details_command must scope get_all_transactions_asc to view_user_id."""
    from bot.commands import details_command

    async def _run():
        group_chat_id = -100888444
        ws = get_or_create_workspace(
            title="Fix 4 Test Group",
            creator_user_id=444000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 444000
        member_uid = 444001
        other_uid = 444002

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        # Insert transactions
        t_member = Transaction(
            amount=75.0,
            transaction_type="SENT",
            person_name="Member Groceries",
            category="Groceries",
            transaction_date="2026-10-15",
            transaction_time="13:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid,
            reference_number="REF_MEMBER_123"
        )
        insert_transaction_with_balance(t_member)

        t_other = Transaction(
            amount=750.0,
            transaction_type="SENT",
            person_name="Other Secret Hotel",
            category="Travel",
            transaction_date="2026-10-15",
            transaction_time="13:05:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid,
            reference_number="REF_OTHER_456"
        )
        insert_transaction_with_balance(t_other)

        # 1. Member with details allowed -> Scoped to own transactions only
        set_feature_permission(ws.id, "details", "member", True)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member4"
        u_member.effective_user.first_name = "Member4"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 4 Test Group"
        u_member.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = []

        await details_command(u_member, ctx)

        assert u_member.message.reply_text.called
        text_reply = u_member.message.reply_text.call_args[0][0]
        assert "Member Groceries" in text_reply
        assert "REF_MEMBER_123" in text_reply
        assert "Other Secret Hotel" not in text_reply
        assert "REF_OTHER_456" not in text_reply

        # 2. Owner path -> Sees all transactions
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 4 Test Group"
        u_owner.message.reply_text = AsyncMock()

        await details_command(u_owner, ctx)
        assert u_owner.message.reply_text.called
        owner_text = u_owner.message.reply_text.call_args[0][0]
        assert "Member Groceries" in owner_text
        assert "Other Secret Hotel" in owner_text

    asyncio.run(_run())


def test_fix5_digest_command_gate():
    """Fix 5: digest_command must be gated on 'report' feature."""
    from bot.commands import digest_command

    async def _run():
        group_chat_id = -100888555
        ws = get_or_create_workspace(
            title="Fix 5 Test Group",
            creator_user_id=555000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 555000
        member_uid = 555001

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")

        # 1. Member with report revoked -> Denied
        set_feature_permission(ws.id, "report", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member5"
        u_member.effective_user.first_name = "Member5"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 5 Test Group"
        u_member.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.args = []

        await digest_command(u_member, ctx)
        assert u_member.message.reply_text.called
        call_args = u_member.message.reply_text.call_args[0][0]
        assert "⛔ <b>Access Denied:</b> Digest feature is restricted for your role in this workspace." in call_args

        # 2. Member with report allowed -> Digest rendered
        set_feature_permission(ws.id, "report", "member", True)
        u_member.message.reply_text.reset_mock()

        await digest_command(u_member, ctx)
        assert u_member.message.reply_text.called
        member_text = u_member.message.reply_text.call_args[0][0]
        assert "Access Denied" not in member_text

        # 3. Owner path -> Digest rendered
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 5 Test Group"
        u_owner.message.reply_text = AsyncMock()

        await digest_command(u_owner, ctx)
        assert u_owner.message.reply_text.called
        owner_text = u_owner.message.reply_text.call_args[0][0]
        assert "Access Denied" not in owner_text

    asyncio.run(_run())


def test_fix6_filter_callback_gate_and_scope():
    """Fix 6: filter: callback branch must be gated on 'history' and scoped to caller's own rows for members."""
    from bot.handlers import _dispatch_callback_query
    from utils.dates import get_current_time_in_tz

    async def _run():
        group_chat_id = -100888666
        ws = get_or_create_workspace(
            title="Fix 6 Test Group",
            creator_user_id=666000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 666000
        member_uid = 666001
        other_uid = 666002

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        now = get_current_time_in_tz()
        today_str = now.date().isoformat()

        # Insert transactions for today
        t_member = Transaction(
            amount=80.0,
            transaction_type="SENT",
            person_name="Member Lunch",
            category="Food & Dining",
            transaction_date=today_str,
            transaction_time="12:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid,
            reference_number="REF_FILTER_MEM_80"
        )
        insert_transaction_with_balance(t_member)

        t_other = Transaction(
            amount=800.0,
            transaction_type="SENT",
            person_name="Other Fancy Dinner",
            category="Food & Dining",
            transaction_date=today_str,
            transaction_time="12:05:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid,
            reference_number="REF_FILTER_OTH_800"
        )
        insert_transaction_with_balance(t_other)

        # 1. Member with history revoked -> Denied
        set_feature_permission(ws.id, "history", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member6"
        u_member.effective_user.first_name = "Member6"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 6 Test Group"

        query_member = MagicMock()
        query_member.data = "filter:today"
        query_member.from_user.id = member_uid
        query_member.answer = AsyncMock()
        query_member.edit_message_text = AsyncMock()
        query_member.message.chat.id = group_chat_id
        u_member.callback_query = query_member

        ctx = MagicMock()

        await _dispatch_callback_query(u_member, ctx)

        query_member.answer.assert_any_call("⛔ History is restricted for your role in this workspace.", show_alert=True)
        query_member.edit_message_text.assert_not_called()

        # 2. Member with history allowed -> Scoped to own transactions only
        set_feature_permission(ws.id, "history", "member", True)

        # 2a. filter:today
        query_member.answer.reset_mock()
        query_member.edit_message_text.reset_mock()
        query_member.data = "filter:today"
        await _dispatch_callback_query(u_member, ctx)
        assert query_member.edit_message_text.called
        text_today = query_member.edit_message_text.call_args[0][0]
        assert "Member Lunch" in text_today
        assert "Other Fancy Dinner" not in text_today

        # 2b. filter:monthly_stats
        query_member.edit_message_text.reset_mock()
        query_member.data = "filter:monthly_stats"
        await _dispatch_callback_query(u_member, ctx)
        assert query_member.edit_message_text.called
        text_stats = query_member.edit_message_text.call_args[0][0]
        assert "₹80" in text_stats
        assert "₹800" not in text_stats
        assert "₹880" not in text_stats

        # 2c. filter:show_ids
        query_member.edit_message_text.reset_mock()
        query_member.data = "filter:show_ids"
        await _dispatch_callback_query(u_member, ctx)
        assert query_member.edit_message_text.called
        text_ids = query_member.edit_message_text.call_args[0][0]
        assert "REF_FILTER_MEM_80" in text_ids
        assert "REF_FILTER_OTH_800" not in text_ids

        # 3. Owner path -> Sees all rows
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 6 Test Group"

        query_owner = MagicMock()
        query_owner.data = "filter:today"
        query_owner.from_user.id = owner_uid
        query_owner.answer = AsyncMock()
        query_owner.edit_message_text = AsyncMock()
        query_owner.message.chat.id = group_chat_id
        u_owner.callback_query = query_owner

        await _dispatch_callback_query(u_owner, ctx)
        assert query_owner.edit_message_text.called
        owner_text = query_owner.edit_message_text.call_args[0][0]
        assert "Member Lunch" in owner_text
        assert "Other Fancy Dinner" in owner_text

    asyncio.run(_run())


def test_fix7_remaining_read_commands_history_gate():
    """Fix 7: today, date, search, amount, filter commands must be gated on 'history'."""
    from bot.commands import (
        today_command,
        date_command,
        search_command,
        amount_command,
        filter_command,
    )
    from utils.dates import get_current_time_in_tz

    async def _run():
        group_chat_id = -100888777
        ws = get_or_create_workspace(
            title="Fix 7 Test Group",
            creator_user_id=777000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 777000
        member_uid = 777001

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")

        now = get_current_time_in_tz()
        today_str = now.date().isoformat()

        # Insert a transaction
        t = Transaction(
            amount=50.0,
            transaction_type="SENT",
            person_name="Chai Point",
            category="Food & Dining",
            transaction_date=today_str,
            transaction_time="10:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t)

        # 1. Member with history revoked -> All 5 must deny
        set_feature_permission(ws.id, "history", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member7"
        u_member.effective_user.first_name = "Member7"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 7 Test Group"
        u_member.message.reply_text = AsyncMock()

        cases = [
            (today_command, []),
            (date_command, ["yesterday"]),
            (search_command, ["Chai"]),
            (amount_command, ["50"]),
            (filter_command, []),
        ]

        denial_text = "⛔ <b>Access Denied:</b> History feature is restricted for your role in this workspace."

        for cmd, args in cases:
            u_member.message.reply_text.reset_mock()
            ctx = MagicMock()
            ctx.args = args
            await cmd(u_member, ctx)
            assert u_member.message.reply_text.called, f"{cmd.__name__} did not reply"
            reply = u_member.message.reply_text.call_args[0][0]
            assert denial_text in reply, f"{cmd.__name__} did not deny: got {reply}"

        # 2. Member with history allowed -> All 5 succeed without denial
        set_feature_permission(ws.id, "history", "member", True)

        for cmd, args in cases:
            u_member.message.reply_text.reset_mock()
            ctx = MagicMock()
            ctx.args = args
            await cmd(u_member, ctx)
            assert u_member.message.reply_text.called
            reply = u_member.message.reply_text.call_args[0][0]
            assert "Access Denied" not in reply, f"{cmd.__name__} unexpectedly denied: {reply}"

        # 3. Owner path -> All 5 succeed without denial
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 7 Test Group"
        u_owner.message.reply_text = AsyncMock()

        for cmd, args in cases:
            u_owner.message.reply_text.reset_mock()
            ctx = MagicMock()
            ctx.args = args
            await cmd(u_owner, ctx)
            assert u_owner.message.reply_text.called
            reply = u_owner.message.reply_text.call_args[0][0]
            assert "Access Denied" not in reply, f"{cmd.__name__} owner denied: {reply}"

    asyncio.run(_run())


def test_fix8_tx_view_callback_details_gate():
    """Fix 8: tx_view: callback branch must be gated on 'details' feature."""
    from bot.handlers import _dispatch_callback_query

    async def _run():
        group_chat_id = -100888888
        ws = get_or_create_workspace(
            title="Fix 8 Test Group",
            creator_user_id=888000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 888000
        member_uid = 888001

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")

        t = Transaction(
            amount=99.0,
            transaction_type="SENT",
            person_name="Tech Store",
            category="Shopping",
            transaction_date="2026-10-15",
            transaction_time="14:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid,
            reference_number="REF_TX_VIEW_99"
        )
        insert_transaction_with_balance(t)
        tx_id = t.id

        # 1. Member with history allowed but details revoked -> Denied
        set_feature_permission(ws.id, "history", "member", True)
        set_feature_permission(ws.id, "details", "member", False)

        u_member = MagicMock()
        u_member.effective_user.id = member_uid
        u_member.effective_user.username = "member8"
        u_member.effective_user.first_name = "Member8"
        u_member.effective_chat.id = group_chat_id
        u_member.effective_chat.type = "group"
        u_member.effective_chat.title = "Fix 8 Test Group"

        query_member = MagicMock()
        query_member.data = f"tx_view:{tx_id}"
        query_member.from_user.id = member_uid
        query_member.answer = AsyncMock()
        query_member.edit_message_text = AsyncMock()
        query_member.message.chat.id = group_chat_id
        u_member.callback_query = query_member

        ctx = MagicMock()

        await _dispatch_callback_query(u_member, ctx)

        query_member.answer.assert_any_call("⛔ Transaction details are restricted for your role in this workspace.", show_alert=True)
        query_member.edit_message_text.assert_not_called()

        # 2. Member with details allowed -> Detail card renders
        set_feature_permission(ws.id, "details", "member", True)
        query_member.answer.reset_mock()
        query_member.edit_message_text.reset_mock()

        await _dispatch_callback_query(u_member, ctx)
        assert query_member.edit_message_text.called
        card_text = query_member.edit_message_text.call_args[0][0]
        assert "REF_TX_VIEW_99" in card_text
        assert "Tech Store" in card_text

        # 3. Owner path -> Detail card renders
        u_owner = MagicMock()
        u_owner.effective_user.id = owner_uid
        u_owner.effective_user.username = "owner"
        u_owner.effective_user.first_name = "Owner"
        u_owner.effective_chat.id = group_chat_id
        u_owner.effective_chat.type = "group"
        u_owner.effective_chat.title = "Fix 8 Test Group"

        query_owner = MagicMock()
        query_owner.data = f"tx_view:{tx_id}"
        query_owner.from_user.id = owner_uid
        query_owner.answer = AsyncMock()
        query_owner.edit_message_text = AsyncMock()
        query_owner.message.chat.id = group_chat_id
        u_owner.callback_query = query_owner

        await _dispatch_callback_query(u_owner, ctx)
        assert query_owner.edit_message_text.called
        owner_card = query_owner.edit_message_text.call_args[0][0]
        assert "REF_TX_VIEW_99" in owner_card

    asyncio.run(_run())


def test_fix9_nav_stats_scoped():
    """Fix 9: nav_stats must scope get_monthly_summary to view_user_id for members."""
    from bot.callbacks.nav import nav_stats
    from utils.dates import get_current_time_in_tz

    async def _run():
        group_chat_id = -100888999
        ws = get_or_create_workspace(
            title="Fix 9 Test Group",
            creator_user_id=999000,
            chat_id=group_chat_id,
            chat_type="group"
        )
        owner_uid = 999000
        member_uid = 999001
        other_uid = 999002

        add_workspace_member(ws.id, owner_uid, role="owner")
        add_workspace_member(ws.id, member_uid, role="member")
        add_workspace_member(ws.id, other_uid, role="member")

        now = get_current_time_in_tz()
        today_str = now.date().isoformat()

        # Member transaction
        t_member = Transaction(
            amount=120.0,
            transaction_type="SENT",
            person_name="Member Chai Walla",
            category="Food & Dining",
            transaction_date=today_str,
            transaction_time="10:00:00",
            workspace_id=ws.id,
            telegram_user_id=member_uid
        )
        insert_transaction_with_balance(t_member)

        # Other user transaction
        t_other = Transaction(
            amount=1200.0,
            transaction_type="SENT",
            person_name="Secret Big Consultant",
            category="Services",
            transaction_date=today_str,
            transaction_time="10:05:00",
            workspace_id=ws.id,
            telegram_user_id=other_uid
        )
        insert_transaction_with_balance(t_other)

        # Allow report for member
        set_feature_permission(ws.id, "report", "member", True)

        query_member = MagicMock()
        query_member.from_user.id = member_uid
        query_member.answer = AsyncMock()
        query_member.edit_message_text = AsyncMock()

        ws_ctx_member = MagicMock()
        ws_ctx_member.role = "member"
        ws_ctx_member.workspace_id = ws.id

        await nav_stats(query_member, MagicMock(), ["nav", "stats"], ws.id, ws_ctx_member)
        assert query_member.edit_message_text.called
        member_text = query_member.edit_message_text.call_args[0][0]

        # Member should only see ₹120, not ₹1200 or ₹1320 or Secret Big Consultant
        assert "₹120" in member_text
        assert "₹1,200" not in member_text
        assert "₹1,320" not in member_text
        assert "Secret Big Consultant" not in member_text

        # Owner should see the full workspace summary
        query_owner = MagicMock()
        query_owner.from_user.id = owner_uid
        query_owner.answer = AsyncMock()
        query_owner.edit_message_text = AsyncMock()

        ws_ctx_owner = MagicMock()
        ws_ctx_owner.role = "owner"
        ws_ctx_owner.workspace_id = ws.id

        await nav_stats(query_owner, MagicMock(), ["nav", "stats"], ws.id, ws_ctx_owner)
        assert query_owner.edit_message_text.called
        owner_text = query_owner.edit_message_text.call_args[0][0]
        assert "₹1,320" in owner_text
        assert "Secret Big Consultant" in owner_text

    asyncio.run(_run())


def test_fix10_supergroup_workspaces_personal_ledger_isolation(monkeypatch):
    """Fix 10: supergroup /workspaces in admin mode must isolate other users' personal DM ledgers."""
    from bot.commands import render_workspaces_view
    from database.queries import get_or_create_workspace, add_workspace_member
    import config

    user_a_id = 1000901
    user_b_id = 1000902
    supergroup_chat_id = -100199999999

    monkeypatch.setattr(config, "TELEGRAM_USER_ID", str(user_a_id))

    grp_ws = get_or_create_workspace(
        title="Engineering Supergroup",
        creator_user_id=user_a_id,
        chat_id=supergroup_chat_id,
        chat_type="group"
    )
    user_b_dm = get_or_create_workspace(
        title="User B Super Secret Ledger",
        creator_user_id=user_b_id,
        chat_id=user_b_id,
        chat_type="dm"
    )
    user_b_dm_id = user_b_dm.id
    add_workspace_member(grp_ws.id, user_a_id, role="admin")

    mock_update = MagicMock()
    mock_update.effective_chat.id = supergroup_chat_id
    mock_update.effective_chat.type = "supergroup"
    mock_update.effective_chat.title = "Engineering Supergroup"
    mock_update.effective_user.id = user_a_id

    text, keyboard = render_workspaces_view(mock_update)

    assert "Engineering Supergroup" in text
    assert "User B Super Secret Ledger" not in text
    assert "Personal Ledgers:" not in text

    for row in keyboard.inline_keyboard:
        for btn in row:
            assert "User B Super Secret" not in btn.text
            if btn.callback_data and btn.callback_data.startswith("ws_switch:"):
                assert btn.callback_data != f"ws_switch:{user_b_dm_id}"


def test_fix11_per_workspace_roles_in_card_and_list():
    """Fix 11: permissions card and list must display per-workspace roles, not blended global role."""
    from database.queries import get_or_create_workspace, add_workspace_member, get_all_users_for_permissions
    from bot.commands import render_user_permission_card, render_permissions_list_payload

    target_uid = 11119999
    ws_a = get_or_create_workspace(
        title="Payment (Group)",
        creator_user_id=11110000,
        chat_id=-10011110000,
        chat_type="group"
    )
    ws_b = get_or_create_workspace(
        title="Nagendra (Personal)",
        creator_user_id=target_uid,
        chat_id=target_uid,
        chat_type="dm"
    )

    add_workspace_member(ws_a.id, target_uid, username="nagendra_test", display_name="Nagendra Test", role="admin")
    add_workspace_member(ws_b.id, target_uid, username="nagendra_test", display_name="Nagendra Test", role="member")

    # 1. get_all_users_for_permissions must return one row PER (user, workspace)
    all_rows = get_all_users_for_permissions()
    user_rows = [r for r in all_rows if r['telegram_user_id'] == target_uid]
    assert len(user_rows) >= 2, f"Expected at least 2 rows for user in different workspaces, got {len(user_rows)}"
    ws_roles = {r.get('workspace_title'): r['role'] for r in user_rows}
    assert ws_roles.get("Payment (Group)") == "admin"
    assert ws_roles.get("Nagendra (Personal)") == "member"

    # 2. render_user_permission_card must list roles in EACH workspace
    card_text, markup = render_user_permission_card(target_uid)
    assert "Payment (Group)" in card_text
    assert "ADMIN" in card_text
    assert "Nagendra (Personal)" in card_text
    assert "MEMBER" in card_text
    assert "Current Role: 🛡️ ADMIN" not in card_text  # No single blended role

    # 3. render_permissions_list_payload must show each role WITH its workspace
    list_text, list_markup = render_permissions_list_payload()
    assert "Payment (Group)" in list_text
    assert "Nagendra (Personal)" in list_text


def test_fix12_perm_set_workspace_scope_and_global_owner():
    """Fix 12: perm_set must be global-owner-only, confirmation must name the workspace, and card must reflect scope."""
    from bot.handlers import handle_callback_query
    from bot.commands import render_user_permission_card
    from database.queries import (
        get_or_create_workspace, add_workspace_member,
        get_workspace_member
    )
    import config

    async def _run():
        global_owner_uid = 8379948573
        non_global_uid = 999888111
        target_uid = 444555666

        old_owner = getattr(config, 'TELEGRAM_USER_ID', None)
        try:
            config.TELEGRAM_USER_ID = str(global_owner_uid)

            chat1_id = -100777111
            chat2_id = -100777222

            ws1 = get_or_create_workspace(
                title="Group Alpha",
                creator_user_id=global_owner_uid,
                chat_id=chat1_id,
                chat_type="group"
            )
            ws2 = get_or_create_workspace(
                title="Group Beta",
                creator_user_id=global_owner_uid,
                chat_id=chat2_id,
                chat_type="group"
            )

            add_workspace_member(ws1.id, global_owner_uid, role="owner")
            add_workspace_member(ws2.id, global_owner_uid, role="owner")

            add_workspace_member(ws1.id, non_global_uid, role="admin")
            add_workspace_member(ws2.id, non_global_uid, role="member")

            add_workspace_member(ws1.id, target_uid, username="target_user", role="member")
            add_workspace_member(ws2.id, target_uid, username="target_user", role="member")

            # 1. Scope line in render_user_permission_card
            card_text, _ = render_user_permission_card(target_uid, workspace_id=ws1.id)
            assert "Group Alpha" in card_text
            assert "Role changes below apply to: <b>Group Alpha</b>" in card_text

            # 2. Non-global owner calling perm_set -> Denied
            u_non_owner = MagicMock()
            u_non_owner.effective_user.id = non_global_uid
            u_non_owner.effective_chat.id = chat1_id
            u_non_owner.effective_chat.type = "group"
            q_non_owner = MagicMock()
            q_non_owner.data = f"perm_set:{target_uid}:admin"
            q_non_owner.from_user.id = non_global_uid
            q_non_owner.answer = AsyncMock()
            q_non_owner.edit_message_text = AsyncMock()
            q_non_owner.message.chat.id = chat1_id
            q_non_owner.message.text = "Some text"
            u_non_owner.callback_query = q_non_owner

            await handle_callback_query(u_non_owner, MagicMock())
            assert q_non_owner.answer.called
            assert "Owner" in q_non_owner.answer.call_args[0][0]
            assert q_non_owner.answer.call_args[1].get('show_alert') is True
            mem1 = get_workspace_member(ws1.id, target_uid)
            assert mem1.role == "member"  # Unchanged!

            # 3. Global owner calling perm_set -> Allowed, names workspace, updates target workspace
            u_owner = MagicMock()
            u_owner.effective_user.id = global_owner_uid
            u_owner.effective_chat.id = chat1_id
            u_owner.effective_chat.type = "group"
            q_owner = MagicMock()
            q_owner.data = f"perm_set:{target_uid}:admin"
            q_owner.from_user.id = global_owner_uid
            q_owner.answer = AsyncMock()
            q_owner.edit_message_text = AsyncMock()
            q_owner.message.chat.id = chat1_id
            q_owner.message.text = "Some text"
            u_owner.callback_query = q_owner

            await handle_callback_query(u_owner, MagicMock())
            q_owner.answer.assert_called_with("✅ Role set to ADMIN in Group Alpha!", show_alert=False)

            # Role updated in ws1, unchanged in ws2
            mem1 = get_workspace_member(ws1.id, target_uid)
            mem2 = get_workspace_member(ws2.id, target_uid)
            assert mem1.role == "admin"
            assert mem2.role == "member"

            # Card re-rendered with updated ws1 role and unchanged ws2 role
            assert q_owner.edit_message_text.called
            edited_card = q_owner.edit_message_text.call_args[0][0]
            assert "Group Alpha: 🛡️ <b>ADMIN</b> — Active" in edited_card
            assert "Group Beta: 👤 <b>MEMBER</b> — Active" in edited_card

        finally:
            config.TELEGRAM_USER_ID = old_owner

    asyncio.run(_run())


def test_fix13_perm_remove_scope_and_confirmation():
    """Fix 13: perm_remove and perm_remove_confirm must name the workspace and card must reflect removal from that workspace."""
    from bot.handlers import handle_callback_query
    from database.queries import (
        get_or_create_workspace, add_workspace_member,
        get_workspace_member
    )
    import config

    async def _run():
        global_owner_uid = 8379948573
        target_uid = 555666777

        old_owner = getattr(config, 'TELEGRAM_USER_ID', None)
        try:
            config.TELEGRAM_USER_ID = str(global_owner_uid)

            chat1_id = -100666111
            chat2_id = -100666222

            ws1 = get_or_create_workspace(
                title="Group Alpha Remove",
                creator_user_id=global_owner_uid,
                chat_id=chat1_id,
                chat_type="group"
            )
            ws2 = get_or_create_workspace(
                title="Group Beta Keep",
                creator_user_id=global_owner_uid,
                chat_id=chat2_id,
                chat_type="group"
            )

            add_workspace_member(ws1.id, global_owner_uid, role="owner")
            add_workspace_member(ws2.id, global_owner_uid, role="owner")

            add_workspace_member(ws1.id, target_uid, username="remove_candidate", role="member")
            add_workspace_member(ws2.id, target_uid, username="remove_candidate", role="member")

            # 1. perm_remove prompt asks for confirmation with workspace name
            u_owner = MagicMock()
            u_owner.effective_user.id = global_owner_uid
            u_owner.effective_chat.id = chat1_id
            u_owner.effective_chat.type = "group"
            q_owner = MagicMock()
            q_owner.data = f"perm_remove:{target_uid}"
            q_owner.from_user.id = global_owner_uid
            q_owner.answer = AsyncMock()
            q_owner.edit_message_text = AsyncMock()
            q_owner.message.chat.id = chat1_id
            q_owner.message.text = "Some text"
            u_owner.callback_query = q_owner

            await handle_callback_query(u_owner, MagicMock())
            assert q_owner.edit_message_text.called
            prompt_text = q_owner.edit_message_text.call_args[0][0]
            assert "Group Alpha Remove" in prompt_text

            # 2. perm_remove_confirm executes removal from ws1 only
            q_confirm = MagicMock()
            q_confirm.data = f"perm_remove_confirm:{target_uid}"
            q_confirm.from_user.id = global_owner_uid
            q_confirm.answer = AsyncMock()
            q_confirm.edit_message_text = AsyncMock()
            q_confirm.message.chat.id = chat1_id
            q_confirm.message.text = "Some text"
            u_owner.callback_query = q_confirm

            await handle_callback_query(u_owner, MagicMock())
            q_confirm.answer.assert_called_with("✅ Member removed from Group Alpha Remove!", show_alert=True)

            # Member is removed from ws1, but still present in ws2
            mem1 = get_workspace_member(ws1.id, target_uid)
            assert mem1 is None

            mem2 = get_workspace_member(ws2.id, target_uid)
            assert mem2 is not None
            assert mem2.role == "member"

            # Final card view re-rendered for target_uid shows user gone from ws1, but still in ws2
            assert q_confirm.edit_message_text.called
            final_card = q_confirm.edit_message_text.call_args[0][0]
            assert "Group Beta Keep" in final_card
            assert "Group Alpha Remove:" not in final_card

        finally:
            config.TELEGRAM_USER_ID = old_owner

    asyncio.run(_run())











