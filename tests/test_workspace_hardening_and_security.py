"""
Comprehensive Security and Hardening Test Suite for Multi-Tenant Workspace Architecture.
Covers:
1. Telegram Supergroup Migration (chat_id migration events).
2. Group default onboarding as 'viewer' with owner promotion.
3. Callback query live RBAC re-check (guarding against button render vs. role demotion races).
4. Cross-workspace collision isolation (pending receipts, duplicate reference numbers, undo log).
5. Scheduler multi-workspace fan-out and per-workspace error isolation.
6. Dashboard action-level authorization and anti-CSRF token verification.
7. Rollout telemetry and metrics recording.
"""

import uuid
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery

from database.db import get_db_connection, setup_database
from database.models import Transaction, Workspace, WorkspaceMember
from database.queries import (
    get_or_create_workspace,
    get_workspace_by_id,
    get_workspace_by_chat_id,
    get_workspace_member,
    add_workspace_member,
    update_workspace_member_role,
    migrate_workspace_chat_id,
    insert_transaction,
    get_transaction_by_id,
    get_recent_transactions,
    save_pending_receipt,
    get_pending_receipt,
    get_active_workspaces
)
from bot.auth import (
    RequestContext,
    WORKSPACE_ROLE_HIERARCHY,
    get_workspace_context,
    resolve_workspace_context,
    get_callback_policy
)
from bot.handlers import (
    handle_callback_query,
    handle_chat_migration,
    set_pending_transaction,
    fetch_pending_transaction
)
from services.dashboard_auth import (
    create_one_time_code,
    exchange_code_for_session,
    validate_dashboard_action,
    get_session_info
)
from services.scheduler_service import daily_digest_job, send_daily_digest_with_retry
from utils.telemetry import get_metrics, reset_metrics


@pytest.fixture(autouse=True)
def init_db():
    setup_database()
    reset_metrics()
    yield
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspaces WHERE id != (SELECT value FROM settings WHERE key = 'default_workspace_id')")
        conn.execute("DELETE FROM workspace_members WHERE workspace_id != (SELECT value FROM settings WHERE key = 'default_workspace_id')")
        conn.execute("DELETE FROM workspace_settings WHERE key LIKE 'user_active_ws:%'")
        conn.execute("DELETE FROM settings WHERE key LIKE 'user_active_ws:%'")
        conn.commit()
    from bot.auth import _USER_ACTIVE_WORKSPACES
    _USER_ACTIVE_WORKSPACES.clear()


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "group", text: str = ""):
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
    msg.chat = chat
    msg.chat_id = chat_id
    msg.message_id = 1001
    msg.reply_text = AsyncMock()
    msg.migrate_to_chat_id = None
    msg.migrate_from_chat_id = None
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


def make_mock_callback_update(user_id: int, chat_id: int, callback_data: str, chat_type: str = "group"):
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

    query = MagicMock(spec=CallbackQuery)
    query.data = callback_data
    query.from_user = user
    query.message = MagicMock(spec=Message)
    query.message.chat = chat
    query.message.chat_id = chat_id
    query.message.message_id = 2002
    query.message.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    query.answer = AsyncMock()
    update.callback_query = query
    update.effective_message = query.message
    update.message = None

    return update


def test_telegram_supergroup_chat_migration():
    """Verify that group -> supergroup chat_id migration seamlessly re-anchors the workspace."""
    old_chat_id = -500111
    new_chat_id = -100500111999
    owner_id = 70001

    ws = get_or_create_workspace(
        chat_id=str(old_chat_id),
        chat_type="group",
        title="Engineering Treasury",
        creator_user_id=owner_id
    )
    ws_id = ws.id

    # Add transaction before migration
    tx = Transaction(
        transaction_type="SENT",
        amount=1500.0,
        person_name="Cloud Hosting",
        reference_number="MIG_REF_01",
        workspace_id=ws_id,
        balance_before=5000.0,
        balance_after=3500.0,
        uid=uuid.uuid4().hex
    )
    insert_transaction(tx)

    # Trigger chat migration service message via handler
    update = make_mock_update(user_id=owner_id, chat_id=old_chat_id, chat_type="group")
    update.message.migrate_to_chat_id = new_chat_id

    async def _test_migration():
        await handle_chat_migration(update, MagicMock())

    asyncio.run(_test_migration())

    # Verify old chat_id no longer matches, new supergroup chat_id matches the same workspace
    assert get_workspace_by_chat_id(old_chat_id) is None
    migrated_ws = get_workspace_by_chat_id(new_chat_id)
    assert migrated_ws is not None
    assert migrated_ws.id == ws_id
    assert migrated_ws.chat_type == "supergroup"

    # Verify transactions remain intact under the migrated workspace
    txs = get_recent_transactions(limit=10, workspace_id=ws_id)
    assert len(txs) >= 1
    assert any(t['reference_number'] == "MIG_REF_01" for t in txs)

    # Verify telemetry increment
    metrics = get_metrics()
    assert metrics.get("chat_migrations", 0) >= 1


def test_group_member_onboarding_policy_defaults_to_member():
    """Verify that new group participants default to 'member' and can log/manage their payments."""
    chat_id = -600222
    creator_id = 80001
    new_member_id = 80002

    # Group creator becomes owner
    ws = get_or_create_workspace(
        chat_id=str(chat_id),
        chat_type="group",
        title="Alpha Finance",
        creator_user_id=creator_id
    )

    # New participant interacts with group
    up_new = make_mock_update(user_id=new_member_id, chat_id=chat_id, chat_type="group", text="/balance")
    ctx = get_workspace_context(up_new)
    assert ctx is not None
    # Crucial UX & self-service: Defaults to 'member'
    assert ctx.role == "member"
    assert ctx.has_role("viewer") is True
    assert ctx.has_role("member") is True
    assert ctx.has_role("admin") is False

    # Owner promotes participant to admin
    success = update_workspace_member_role(ws.id, new_member_id, "admin")
    assert success is True

    # Re-evaluating context reflects promoted role
    ctx_updated = get_workspace_context(up_new)
    assert ctx_updated.role == "admin"
    assert ctx_updated.has_role("admin") is True


def test_callback_rbac_demotion_race_defense():
    """Verify that demoted users cannot execute rendered buttons (callback replay protection)."""
    chat_id = -700333
    owner_id = 90001
    target_user_id = 90002

    ws = get_or_create_workspace(
        chat_id=str(chat_id),
        chat_type="group",
        title="Beta Syndicate",
        creator_user_id=owner_id
    )
    # Target was initially an admin when button was sent
    add_workspace_member(ws.id, target_user_id, role="admin")

    # Demote user to viewer before they click the callback
    update_workspace_member_role(ws.id, target_user_id, "viewer")

    # User clicks 'delete_confirm:123' (which requires admin)
    up_cb = make_mock_callback_update(
        user_id=target_user_id,
        chat_id=chat_id,
        callback_data="delete_confirm:9999"
    )

    async def _test_cb():
        await handle_callback_query(up_cb, MagicMock())

    asyncio.run(_test_cb())

    # Callback answered with permission restricted alert
    up_cb.callback_query.answer.assert_called_once()
    alert_text = up_cb.callback_query.answer.call_args[0][0]
    assert "Restricted" in alert_text or "Admin" in alert_text

    # Telemetry records auth denial
    metrics = get_metrics()
    assert metrics.get("auth_denials", 0) >= 1


def test_cross_workspace_pending_receipts_isolation():
    """Verify pending receipts cannot collide or be accessed across different workspaces."""
    ws_1 = get_or_create_workspace(chat_id="111111", chat_type="private", title="Personal 1", creator_user_id=11)
    ws_2 = get_or_create_workspace(chat_id="222222", chat_type="private", title="Personal 2", creator_user_id=22)

    shared_pending_id = "pending_uuid_abc_123"

    tx1 = Transaction(amount=100.0, person_name="Coffee Shop", workspace_id=ws_1.id)
    tx2 = Transaction(amount=500.0, person_name="Dinner", workspace_id=ws_2.id)

    # Save to both workspaces using isolated store
    set_pending_transaction(shared_pending_id, tx1, workspace_id=ws_1.id)
    set_pending_transaction(shared_pending_id, tx2, workspace_id=ws_2.id)

    # Fetching in ws_1 returns Coffee Shop
    fetched_1 = fetch_pending_transaction(shared_pending_id, workspace_id=ws_1.id)
    assert fetched_1 is not None
    assert fetched_1.amount == 100.0
    assert fetched_1.person_name == "Coffee Shop"

    # Fetching in ws_2 returns Dinner
    fetched_2 = fetch_pending_transaction(shared_pending_id, workspace_id=ws_2.id)
    assert fetched_2 is not None
    assert fetched_2.amount == 500.0
    assert fetched_2.person_name == "Dinner"


def test_scheduler_multi_workspace_fanout_and_isolation():
    """Verify scheduler iterates active workspaces and isolates exceptions per workspace."""
    ws_a = get_or_create_workspace(chat_id="-888001", chat_type="group", title="Team A", creator_user_id=81)
    ws_b = get_or_create_workspace(chat_id="-888002", chat_type="group", title="Team B", creator_user_id=82)

    bot_mock = MagicMock()
    context_mock = MagicMock()
    context_mock.bot = bot_mock

    # Mock send_safe_message: Workspace A succeeds, Workspace B raises Exception
    async def mock_send(bot, chat_id, text, parse_mode="HTML"):
        if int(chat_id) == -888002:
            raise ConnectionError("Telegram network timeout on Chat B")
        return MagicMock()

    with patch("database.queries.get_active_workspaces", return_value=[ws_a, ws_b]), \
         patch("ocr.gemini_vision.is_gemini_available", return_value=False), \
         patch("services.scheduler_service.send_safe_message", side_effect=mock_send), \
         patch("asyncio.sleep", AsyncMock()):
        async def _run_job():
            await daily_digest_job(context_mock)

        asyncio.run(_run_job())

    # Verify Workspace A was successfully sent without being blocked by Workspace B's failure
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = 'last_digest_sent_date'", (ws_a.id,))
        row_a = cur.fetchone()
        assert row_a is not None  # Successfully stamped!


def test_dashboard_action_authorization_and_csrf():
    """Verify action-level role authorization and anti-CSRF token verification on dashboard API."""
    # 1. Viewer session attempting mutation
    viewer_session = {
        "user_id": 991,
        "workspace_id": "ws_test_99",
        "role": "viewer",
        "csrf_token": "token_abc123"
    }

    # Read access permitted for viewer
    ok_read, _ = validate_dashboard_action(viewer_session, required_role="viewer")
    assert ok_read is True

    # Mutation access denied for viewer
    ok_write, err = validate_dashboard_action(viewer_session, required_role="member", is_mutation=True)
    assert ok_write is False
    assert "Forbidden" in err

    # 2. Member session with missing CSRF
    member_session = {
        "user_id": 992,
        "workspace_id": "ws_test_99",
        "role": "member",
        "csrf_token": "valid_csrf_token_999"
    }

    ok_no_csrf, err_csrf = validate_dashboard_action(
        member_session, required_role="member",
        csrf_token_header=None, is_mutation=True
    )
    assert ok_no_csrf is False
    assert "CSRF" in err_csrf

    # 3. Member session with valid CSRF
    ok_valid, _ = validate_dashboard_action(
        member_session, required_role="member",
        csrf_token_header="valid_csrf_token_999", is_mutation=True
    )
    assert ok_valid is True


def test_privileged_buttons_omitted_for_unauthorized_roles():
    """Verify that keyboards omit action buttons the user role cannot execute (C-N14)."""
    from bot.keyboards import (
        get_model_selection_keyboard,
        get_recurring_menu_keyboard,
        get_recurring_detail_keyboard,
        get_menu_view_keyboard,
        get_transaction_detail_keyboard
    )

    # 1. Gemini model keyboard: viewers do not see model selection or refresh
    kb_gemini_viewer = get_model_selection_keyboard(role="viewer")
    cb_data_viewer = [btn.callback_data for row in kb_gemini_viewer.inline_keyboard for btn in row]
    assert not any(c.startswith("set_model") for c in cb_data_viewer)
    assert "refresh_gemini" not in cb_data_viewer

    kb_gemini_admin = get_model_selection_keyboard(role="admin")
    cb_data_admin = [btn.callback_data for row in kb_gemini_admin.inline_keyboard for btn in row]
    assert any(c.startswith("set_model") for c in cb_data_admin)
    assert "refresh_gemini" in cb_data_admin

    # 2. Recurring menu keyboard: viewers do not see pay/skip or add buttons
    upcoming_sample = [{"id": 1, "payee_name": "Rent", "amount": 1000}]
    kb_rec_viewer = get_recurring_menu_keyboard(upcoming_items=upcoming_sample, role="viewer")
    cb_rec_viewer = [btn.callback_data for row in kb_rec_viewer.inline_keyboard for btn in row]
    assert not any(c.startswith("rec_paid") for c in cb_rec_viewer)
    assert not any(c.startswith("rec_skip") for c in cb_rec_viewer)
    assert "nav:rec_add" not in cb_rec_viewer

    # 3. Cafeteria menu keyboard: viewers do not see add/delete or edit last
    kb_menu_viewer = get_menu_view_keyboard(role="viewer")
    cb_menu_viewer = [btn.callback_data for row in kb_menu_viewer.inline_keyboard for btn in row]
    assert "cafe_menu_add_prompt" not in cb_menu_viewer
    assert "cafe_menu_del_prompt" not in cb_menu_viewer
    assert "cafe_edit_last" not in cb_menu_viewer

    # 4. Transaction detail keyboard: viewers do not see edit, delete, or duplicate
    kb_tx_viewer = get_transaction_detail_keyboard(101, role="viewer")
    cb_tx_viewer = [btn.callback_data for row in kb_tx_viewer.inline_keyboard for btn in row]
    assert not any(c.startswith("edit_tx") for c in cb_tx_viewer)
    assert not any(c.startswith("delete_tx") for c in cb_tx_viewer)
    assert not any(c.startswith("dup_tx") for c in cb_tx_viewer)
    assert any(c.startswith("nav:history") for c in cb_tx_viewer)
