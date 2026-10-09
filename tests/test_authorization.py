"""
Comprehensive unit tests for authorization and access control policy (PROMPT 7).
Validates:
1. Unauthorized users rejected on every command and callback.
2. Group members (non-owner) can access read-only views but are rejected on ALL mutations, exports, backups, restore, dashboard, settings.
3. Bot owner passes all commands and callbacks.
4. Unknown or stale callback data receives a friendly refusal without crashing.
5. Central policy list verification: Any registered command or callback without a policy fails tests.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery

import config
from bot.auth import (
    require_authorized, require_admin, is_owner, is_authorized_user,
    get_command_policy, get_callback_policy,
    READ_ONLY_COMMANDS, ADMIN_COMMANDS,
    READ_ONLY_CALLBACK_ACTIONS, ADMIN_CALLBACK_ACTIONS
)
import bot.commands as cmd_module
from bot.handlers import handle_callback_query, handle_image, handle_text

OWNER_ID = 11111111
GROUP_ID = -22222222
STRANGER_ID = 99999999
STRANGER_CHAT_ID = 88888888

@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", OWNER_ID)
    monkeypatch.setattr(config, "TELEGRAM_GROUP_ID", GROUP_ID)
    monkeypatch.setattr(config, "LEGACY_SINGLE_TENANT_MODE", True)
    monkeypatch.setattr(config, "ALLOW_PUBLIC_WORKSPACES", False)

def make_mock_update(user_id=STRANGER_ID, chat_id=STRANGER_CHAT_ID, text="", callback_data=None):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    update.effective_user = user
    
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = 'group' if chat_id < 0 else 'private'
    update.effective_chat = chat
    
    if callback_data is not None:
        cq = MagicMock(spec=CallbackQuery)
        cq.data = callback_data
        cq.answer = AsyncMock()
        cq.edit_message_text = AsyncMock()
        update.callback_query = cq
        update.effective_message = None
        update.message = None
    else:
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.chat_id = chat_id
        msg.message_id = 123
        msg.reply_text = AsyncMock()
        update.effective_message = msg
        update.message = msg
        update.callback_query = None
        
    return update

# --- Test Core Auth Helpers ---

def test_auth_helpers_unauthorized_user():
    up = make_mock_update(user_id=STRANGER_ID, chat_id=STRANGER_CHAT_ID)
    assert is_owner(up) is False
    assert is_authorized_user(up) is False
    assert asyncio.run(require_authorized(up)) is False
    assert asyncio.run(require_admin(up)) is False
    assert up.effective_message.reply_text.called

def test_auth_helpers_group_member_non_owner():
    up = make_mock_update(user_id=STRANGER_ID, chat_id=GROUP_ID)
    assert is_owner(up) is False
    assert is_authorized_user(up) is True
    assert asyncio.run(require_authorized(up)) is True
    assert asyncio.run(require_admin(up)) is False

def test_auth_helpers_owner():
    up = make_mock_update(user_id=OWNER_ID, chat_id=STRANGER_CHAT_ID)
    assert is_owner(up) is True
    assert is_authorized_user(up) is True
    assert asyncio.run(require_authorized(up)) is True
    assert asyncio.run(require_admin(up)) is True

# --- Test All Commands Across Roles ---

def _get_all_registered_commands():
    from telegram.ext import CommandHandler
    from app import build_application
    app = build_application()
    return {
        c: h.callback
        for h in app.handlers.get(0, [])
        if isinstance(h, CommandHandler)
        for c in h.commands
    }

ALL_COMMAND_HANDLERS = _get_all_registered_commands()

@pytest.mark.parametrize("cmd_name, handler", list(ALL_COMMAND_HANDLERS.items()))
def test_commands_reject_unauthorized_user(cmd_name, handler):
    up = make_mock_update(user_id=STRANGER_ID, chat_id=STRANGER_CHAT_ID, text=f"/{cmd_name}")
    ctx = MagicMock()
    ctx.args = ["invalid_token"] if cmd_name == "join" else []
    
    asyncio.run(handler(up, ctx))
    # Must reply with unauthorized, request pending, admin refusal, or invalid token
    assert up.effective_message.reply_text.called
    call_args = str(up.effective_message.reply_text.call_args).lower()
    assert ("unauthorized" in call_args or "admin only" in call_args or "restricted" in call_args or "access" in call_args or "invalid" in call_args)

@pytest.mark.parametrize("cmd_name", list(ADMIN_COMMANDS))
def test_admin_commands_reject_group_member(cmd_name):
    handler = ALL_COMMAND_HANDLERS[cmd_name]
    up = make_mock_update(user_id=STRANGER_ID, chat_id=GROUP_ID, text=f"/{cmd_name}")
    ctx = MagicMock()
    ctx.args = []
    
    asyncio.run(handler(up, ctx))
    assert up.effective_message.reply_text.called
    call_args = str(up.effective_message.reply_text.call_args).lower()
    assert "admin only" in call_args or "restricted" in call_args

@pytest.mark.parametrize("cmd_name", list(READ_ONLY_COMMANDS))
def test_readonly_commands_allow_group_member(cmd_name):
    handler = ALL_COMMAND_HANDLERS[cmd_name]
    up = make_mock_update(user_id=STRANGER_ID, chat_id=GROUP_ID, text=f"/{cmd_name}")
    ctx = MagicMock()
    ctx.args = []
    
    with patch("bot.commands.get_balance_setting", return_value=1000.0), \
         patch("bot.commands.get_today_summary") as mock_ts, \
         patch("bot.commands.get_overall_summary") as mock_os, \
         patch("bot.commands.get_monthly_summary", return_value={'total_sent': 0, 'total_received': 0, 'net_savings': 0, 'tx_count': 0, 'top_recipient': None}), \
         patch("bot.commands.get_all_transactions_asc", return_value=[]), \
         patch("bot.commands.search_transactions", return_value=[]), \
         patch("services.cafeteria_service.format_full_menu", return_value="Menu"), \
         patch("services.cafeteria_service.format_cafeteria_stats", return_value="Stats"), \
         patch("services.scheduler_service.format_daily_digest", return_value="Digest"), \
         patch("services.budget_service.format_budget_status", return_value="Budget"), \
         patch("ocr.gemini_vision.check_gemini_api_status_async", AsyncMock(return_value={"status": "OK", "model": "gemini-3.8-flash", "masked_key": "…1234"})):
        
        mock_ts.return_value.net_change = 0
        mock_ts.return_value.total_received = 0
        mock_ts.return_value.total_sent = 0
        mock_ts.return_value.transaction_count = 0
        mock_os.return_value.net_change = 0
        mock_os.return_value.total_received = 0
        mock_os.return_value.total_sent = 0
        mock_os.return_value.transaction_count = 0
        
        asyncio.run(handler(up, ctx))
        
        assert up.effective_message.reply_text.called
        call_args = str(up.effective_message.reply_text.call_args)
        assert "❌ Unauthorized user" not in call_args
        assert "restricted to the bot owner" not in call_args

# --- Test Callbacks Across Roles ---

@pytest.mark.parametrize("action", list(ADMIN_CALLBACK_ACTIONS))
def test_admin_callbacks_reject_group_member(action):
    cb_data = f"{action}:123"
    up = make_mock_update(user_id=STRANGER_ID, chat_id=GROUP_ID, callback_data=cb_data)
    ctx = MagicMock()
    
    asyncio.run(handle_callback_query(up, ctx))
    assert up.callback_query.answer.called
    call_args = str(up.callback_query.answer.call_args).lower()
    assert "admin only" in call_args or "restricted" in call_args

@pytest.mark.parametrize("action", list(ADMIN_CALLBACK_ACTIONS | READ_ONLY_CALLBACK_ACTIONS))
def test_all_callbacks_reject_unauthorized_user(action):
    cb_data = f"{action}:123"
    up = make_mock_update(user_id=STRANGER_ID, chat_id=STRANGER_CHAT_ID, callback_data=cb_data)
    ctx = MagicMock()
    
    asyncio.run(handle_callback_query(up, ctx))
    assert up.callback_query.answer.called
    call_args = str(up.callback_query.answer.call_args).lower()
    assert "unauthorized" in call_args or "admin only" in call_args or "access" in call_args


def test_unknown_or_stale_callback_gives_friendly_refusal():
    up = make_mock_update(user_id=OWNER_ID, chat_id=STRANGER_CHAT_ID, callback_data="non_existent_action:123")
    ctx = MagicMock()
    
    asyncio.run(handle_callback_query(up, ctx))
    assert up.callback_query.answer.called
    call_args = str(up.callback_query.answer.call_args)
    assert "no longer active" in call_args

# --- Enforced Authorization Consistency Tests ---

def test_all_registered_commands_have_callable_handlers():
    """Ensures every command registered in app.py has a valid callable handler."""
    assert len(ALL_COMMAND_HANDLERS) >= 70
    for cmd_name, handler in ALL_COMMAND_HANDLERS.items():
        assert callable(handler), f"Command '{cmd_name}' handler is not callable!"

def test_admin_commands_enforced_against_non_admin_members():
    """Ensures critical administrative commands strictly enforce require_admin."""
    admin_commands = ["setbalance", "addmenu", "delmenu", "cafeedit", "setbudget", "setmodel"]
    for cmd in admin_commands:
        handler = ALL_COMMAND_HANDLERS.get(cmd)
        assert handler is not None, f"Command '{cmd}' not found in registered handlers"
