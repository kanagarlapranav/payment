import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery
from telegram.ext import ContextTypes

from database.db import setup_database, get_db_connection
from database.queries import (
    create_access_request, get_access_request, update_access_request_status,
    get_all_users_for_permissions, set_user_permission_and_role,
    get_workspace_member, get_or_create_workspace, add_workspace_member,
    get_default_workspace_id
)
from bot.auth import (
    is_owner, is_authorized_user, require_authorized, require_admin, require_owner,
    COMMAND_ROLE_POLICY, ADMIN_CALLBACK_ACTIONS
)
import bot.commands as cmd_module
import bot.handlers as handlers_module


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Sets up a clean temporary database for tests."""
    db_file = tmp_path / "test_access.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("config.TELEGRAM_USER_ID", 8379948573)
    monkeypatch.setattr("config.TELEGRAM_GROUP_ID", -1009999999999)
    monkeypatch.setattr("config.ALLOW_PUBLIC_WORKSPACES", False)
    setup_database()
    yield


def test_gemini_and_ai_commands_restricted_to_admin():
    """Verifies that gemini, quota, ai, insights, setmodel are restricted to admin role."""
    for cmd in ["gemini", "geministatus", "quota", "ai", "insights", "setmodel", "model"]:
        assert COMMAND_ROLE_POLICY.get(cmd) == "admin", f"{cmd} must be admin-only"

    assert "refresh_gemini" in ADMIN_CALLBACK_ACTIONS, "refresh_gemini must be admin callback action"
    assert "set_model" in ADMIN_CALLBACK_ACTIONS, "set_model must be admin callback action"


def test_permissions_and_roles_restricted_to_owner():
    """Verifies that permissions and roles are owner-only."""
    for cmd in ["permissions", "roles", "setrole", "setbalance", "restore"]:
        assert COMMAND_ROLE_POLICY.get(cmd) == "owner", f"{cmd} must be owner-only"


@pytest.mark.anyio
async def test_unauthorized_user_triggers_access_request_and_owner_alert():
    """When a new stranger opens the bot and sends /start, an access request is created and owner is alerted."""
    stranger_uid = 999111222
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = stranger_uid
    user.username = "test_friend"
    user.full_name = "Friend User"
    chat = MagicMock(spec=Chat)
    chat.id = stranger_uid
    chat.type = "private"
    update.effective_user = user
    update.effective_chat = chat
    update.callback_query = None

    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.effective_message = message
    update.message = message

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    update.get_bot.return_value = mock_bot

    # Calling require_authorized for stranger
    authorized = await require_authorized(update)
    assert authorized is False

    # User received pending access submission notice
    message.reply_text.assert_called_once()
    assert "Access Request Submitted" in message.reply_text.call_args[0][0]

    # Owner received notification with approval buttons
    mock_bot.send_message.assert_called_once()
    owner_call_args = mock_bot.send_message.call_args
    assert owner_call_args.kwargs['chat_id'] == 8379948573
    assert "New User Access Request" in owner_call_args.kwargs['text']
    assert str(stranger_uid) in owner_call_args.kwargs['text']

    # Database records request as 'pending'
    req = get_access_request(stranger_uid)
    assert req is not None
    assert req['status'] == 'pending'
    assert req['username'] == 'test_friend'


@pytest.mark.anyio
async def test_owner_approves_and_revokes_access():
    """Verifies owner can approve as member and revoke access via queries and callbacks."""
    target_uid = 888777666
    create_access_request(target_uid, "bob_test", "Bob", 888777666, "private")

    # Grant member role
    set_user_permission_and_role(target_uid, "member", is_active=True)
    req = get_access_request(target_uid)
    assert req['status'] == 'approved'

    # Verify user in permissions listing
    all_users = get_all_users_for_permissions()
    target_in_list = next((u for u in all_users if u['telegram_user_id'] == target_uid), None)
    assert target_in_list is not None

    # Revoke access
    set_user_permission_and_role(target_uid, "viewer", is_active=False)
    req_revoked = get_access_request(target_uid)
    assert req_revoked['status'] == 'rejected'


@pytest.mark.anyio
async def test_role_aware_help_hides_admin_and_owner_sections_from_member():
    """Verifies regular members only see tracking/balance/cafeteria and NOT gemini or admin commands in /help."""
    member_uid = 777666555
    group_chat_id = -1009999999999
    
    # Pre-create workspace owned by primary bot owner (8379948573)
    ws = get_or_create_workspace(
        chat_id=group_chat_id,
        chat_type="supergroup",
        title="Test Group",
        creator_user_id=8379948573
    )
    # Add target user as regular member
    add_workspace_member(ws.id, member_uid, username="regular_member", role="member")

    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = member_uid
    user.username = "regular_member"
    chat = MagicMock(spec=Chat)
    chat.id = group_chat_id
    chat.type = "supergroup"
    update.effective_user = user
    update.effective_chat = chat
    update.callback_query = None

    message = MagicMock(spec=Message)
    message.reply_text = AsyncMock()
    update.effective_message = message
    update.message = message

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cmd_module.help_command(update, context)

    message.reply_text.assert_called_once()
    help_body = message.reply_text.call_args[0][0]

    # Member should see tracking & balance
    assert "Tracking & Logging Expenses" in help_body
    assert "/balance" in help_body

    # Member should NOT see Gemini status or owner controls
    assert "Management & Analytics (Admin Only)" not in help_body
    assert "Owner Controls & Governance" not in help_body
    assert "/gemini" not in help_body
    assert "/permissions" not in help_body


@pytest.mark.anyio
async def test_owner_can_set_admin_role_for_member_including_nagendra():
    """Verifies that clicking 'Admin' button in /permissions successfully assigns admin role and updates card."""
    owner_uid = 8379948573
    target_uid = 8343764796  # Nagendra
    group_chat_id = -1009999999999

    ws = get_or_create_workspace(
        chat_id=group_chat_id,
        chat_type="supergroup",
        title="Test Workspace",
        creator_user_id=owner_uid
    )
    add_workspace_member(ws.id, target_uid, username="nagendra", display_name="Nagendra", role="member")

    # 1. Verify initial card shows Member
    card_text, markup = cmd_module.render_user_permission_card(target_uid)
    assert "MEMBER" in card_text
    # Find Admin button callback
    admin_btn = None
    for row in markup.inline_keyboard:
        for btn in row:
            if "Admin" in btn.text:
                admin_btn = btn
    assert admin_btn is not None
    assert admin_btn.callback_data == f"perm_set:{target_uid}:admin"

    # 2. Simulate owner clicking 'Admin' button
    update = MagicMock(spec=Update)
    query = MagicMock(spec=CallbackQuery)
    query.data = f"perm_set:{target_uid}:admin"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.reply_markup = markup
    query.edit_message_text = AsyncMock()

    user = MagicMock(spec=User)
    user.id = owner_uid
    user.username = "owner"
    query.from_user = user
    update.callback_query = query
    update.effective_user = user
    update.effective_chat = MagicMock()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await handlers_module.handle_callback_query(update, context)

    # Verify query.answer was called with success message
    query.answer.assert_called()
    call_args = query.answer.call_args[0]
    assert len(call_args) > 0 and "ADMIN" in call_args[0]

    # Verify message was updated with ADMIN card
    query.edit_message_text.assert_called()
    updated_card = query.edit_message_text.call_args[0][0]
    assert "ADMIN" in updated_card
    assert "🛡️" in updated_card

    # Verify member in database is now admin
    member = get_workspace_member(ws.id, target_uid)
    assert member.role == "admin"


@pytest.mark.anyio
async def test_admin_role_precedence_over_member_in_permissions_card():
    """Verifies that a user having admin role in one workspace is displayed as ADMIN in permissions, not masked by alphabetical member."""
    target_uid = 777111222
    ws1 = get_or_create_workspace(chat_id="11111", chat_type="group", title="WS1", creator_user_id=8379948573)
    ws2 = get_or_create_workspace(chat_id="22222", chat_type="group", title="WS2", creator_user_id=8379948573)

    add_workspace_member(ws1.id, target_uid, username="test_user", role="member")
    add_workspace_member(ws2.id, target_uid, username="test_user", role="admin")

    users = get_all_users_for_permissions()
    target_user = next((u for u in users if u['telegram_user_id'] == target_uid), None)
    assert target_user is not None
    assert target_user['role'] == "admin"

    card_text, markup = cmd_module.render_user_permission_card(target_uid)
    assert "ADMIN" in card_text


@pytest.mark.anyio
async def test_is_owner_always_true_for_global_owner_in_private_dm():
    """Verifies that global bot owner (TELEGRAM_USER_ID) is recognized as owner even in private DM with no personal workspace."""
    owner_id = 8379948573
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = owner_id
    chat = MagicMock(spec=Chat)
    chat.id = owner_id
    chat.type = "private"
    update.effective_user = user
    update.effective_chat = chat
    update.callback_query = None

    assert is_owner(update) is True
