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

ALL_COMMAND_HANDLERS = {
    "start": cmd_module.start_command,
    "balance": cmd_module.balance_command,
    "today": cmd_module.today_command,
    "history": cmd_module.history_command,
    "last5": cmd_module.last5_command,
    "recent": cmd_module.last5_command,
    "details": cmd_module.details_command,
    "ids": cmd_module.details_command,
    "date": cmd_module.date_command,
    "search": cmd_module.search_command,
    "find": cmd_module.search_command,
    "amount": cmd_module.amount_command,
    "amt": cmd_module.amount_command,
    "monthly": cmd_module.monthly_command,
    "stats": cmd_module.monthly_command,
    "filter": cmd_module.filter_command,
    "sort": cmd_module.sort_command,
    "chatid": cmd_module.chatid_command,
    "help": cmd_module.help_command,
    "insights": cmd_module.insights_command,
    "budget": cmd_module.budget_command,
    "digest": cmd_module.digest_command,
    "menu": cmd_module.menu_command,
    "cafeteria": cmd_module.menu_command,
    "canteen": cmd_module.menu_command,
    "cafestats": cmd_module.cafestats_command,
    "cafespends": cmd_module.cafestats_command,
    "edit": cmd_module.edit_command,
    "delete": cmd_module.delete_command,
    "setbalance": cmd_module.setbalance_command,
    "export": cmd_module.export_command,
    "report": cmd_module.export_command,
    "statement": cmd_module.export_command,
    "setbudget": cmd_module.setbudget_command,
    "dashboard": cmd_module.dashboard_command,
    "cafeedit": cmd_module.cafeedit_command,
    "editcafe": cmd_module.cafeedit_command,
    "addmenu": cmd_module.addmenu_command,
    "delmenu": cmd_module.delmenu_command,
    "restore": cmd_module.restore_command,
    "importbackup": cmd_module.restore_command,
    "backup": cmd_module.backup_command,
    "backupnow": cmd_module.backup_command,
    "undo": cmd_module.undo_command,
    "revert": cmd_module.undo_command,
    "gemini": cmd_module.geministatus_command,
    "geministatus": cmd_module.geministatus_command,
    "quota": cmd_module.geministatus_command,
    "ai": cmd_module.geministatus_command,
    "status": cmd_module.geministatus_command,
    "setmodel": cmd_module.setmodel_command,
    "model": cmd_module.setmodel_command,
}

@pytest.mark.parametrize("cmd_name, handler", list(ALL_COMMAND_HANDLERS.items()))
def test_commands_reject_unauthorized_user(cmd_name, handler):
    up = make_mock_update(user_id=STRANGER_ID, chat_id=STRANGER_CHAT_ID, text=f"/{cmd_name}")
    ctx = MagicMock()
    ctx.args = []
    
    asyncio.run(handler(up, ctx))
    # Must reply with unauthorized or admin refusal
    assert up.effective_message.reply_text.called
    call_args = str(up.effective_message.reply_text.call_args)
    assert ("Unauthorized" in call_args or "Admin Only" in call_args or "restricted" in call_args)

@pytest.mark.parametrize("cmd_name", list(ADMIN_COMMANDS))
def test_admin_commands_reject_group_member(cmd_name):
    handler = ALL_COMMAND_HANDLERS[cmd_name]
    up = make_mock_update(user_id=STRANGER_ID, chat_id=GROUP_ID, text=f"/{cmd_name}")
    ctx = MagicMock()
    ctx.args = []
    
    asyncio.run(handler(up, ctx))
    assert up.effective_message.reply_text.called
    call_args = str(up.effective_message.reply_text.call_args)
    assert "Admin Only" in call_args or "restricted" in call_args

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
    call_args = str(up.callback_query.answer.call_args)
    assert "Admin Only" in call_args or "restricted" in call_args

@pytest.mark.parametrize("action", list(ADMIN_CALLBACK_ACTIONS | READ_ONLY_CALLBACK_ACTIONS))
def test_all_callbacks_reject_unauthorized_user(action):
    cb_data = f"{action}:123"
    up = make_mock_update(user_id=STRANGER_ID, chat_id=STRANGER_CHAT_ID, callback_data=cb_data)
    ctx = MagicMock()
    
    asyncio.run(handle_callback_query(up, ctx))
    assert up.callback_query.answer.called
    call_args = str(up.callback_query.answer.call_args)
    assert "Unauthorized" in call_args or "Admin Only" in call_args

def test_unknown_or_stale_callback_gives_friendly_refusal():
    up = make_mock_update(user_id=OWNER_ID, chat_id=STRANGER_CHAT_ID, callback_data="non_existent_action:123")
    ctx = MagicMock()
    
    asyncio.run(handle_callback_query(up, ctx))
    assert up.callback_query.answer.called
    call_args = str(up.callback_query.answer.call_args)
    assert "no longer active" in call_args

# --- Central Policy Consistency Tests ---

def test_all_registered_commands_have_explicit_policy():
    """Ensures no new command is added to the bot without being classified into policy sets."""
    for cmd in ALL_COMMAND_HANDLERS.keys():
        policy = get_command_policy(cmd)
        assert policy in ('admin', 'read_only'), f"Command '{cmd}' is missing an access policy in auth.py!"

def test_command_policies_disjoint():
    """Ensures no command is accidentally marked as both admin and read_only."""
    intersection = READ_ONLY_COMMANDS.intersection(ADMIN_COMMANDS)
    assert len(intersection) == 0, f"Commands in both sets: {intersection}"

def test_callback_policies_disjoint():
    """Ensures no callback prefix is accidentally marked as both admin and read_only."""
    intersection = READ_ONLY_CALLBACK_ACTIONS.intersection(ADMIN_CALLBACK_ACTIONS)
    assert len(intersection) == 0, f"Callbacks in both sets: {intersection}"
