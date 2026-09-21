"""
Central Authorization and Access Control Module.
Enforces strict owner-only administrative access and group read-only access.
"""

from typing import Optional
from telegram import Update
import config
from config import logger

# Command Policies: Explicit mapping of every bot command to its access policy
READ_ONLY_COMMANDS = {
    "start", "balance", "today", "history", "last5", "recent", "details", "ids",
    "date", "search", "find", "amount", "amt", "monthly", "stats", "filter",
    "sort", "chatid", "help", "menu", "cafeteria", "canteen", "cafestats",
    "cafespends", "budget", "digest", "insights"
}

ADMIN_COMMANDS = {
    "edit", "delete", "setbalance", "export", "report", "statement",
    "restore", "importbackup", "undo", "revert", "setbudget", "addmenu", "delmenu",
    "cafeedit", "editcafe", "dashboard"
}

# Callback Action Prefix Policies
READ_ONLY_CALLBACK_ACTIONS = {
    "nav", "filter", "sort", "cafe_stats", "cafe_view_menu", "tx_view"
}

ADMIN_CALLBACK_ACTIONS = {
    # Receipt Card Actions & Pending Edits
    "save_p", "edit_p", "ep_field", "ep_back", "cat_p", "set_pcat", "cancel_p",
    # Undo & Quick Add & Duplicate
    "undo_tx", "quick_add", "undo_action", "dup_tx", "qa_payee",
    # Legacy Confirm / Cancel
    "confirm_tx", "cancel_tx",
    # Edit / Delete Selection & Prompts
    "select_edit", "select_edit_cancel", "select_delete", "select_delete_cancel",
    "edit_field", "edit_cancel", "delete_confirm", "delete_cancel", "correct_amount",
    "edit_tx", "delete_tx",
    # Export File Formats
    "export_file",
    # Cafeteria Mutations & Tagging
    "cafe_pick", "cafe_mode", "cafe_custom_prompt", "cafe_cart_add",
    "cafe_cart_clear", "cafe_cart_done", "cafe_cat", "cafe_back",
    "cafe_addon", "cafe_edit", "cafe_menu_add_prompt", "cafe_menu_del_prompt",
    "cafe_del_item", "cafe_del_cancel", "cafe_edit_last", "cafe_skip",
    # Backup & Restore Confirmation
    "restore_confirm", "restore_cancel", "backup_now"
}

def get_effective_user_id(update: Update) -> Optional[int]:
    """Extracts integer user_id from update if present."""
    if not update:
        return None
    user = update.effective_user
    if not user:
        return None
    try:
        return int(user.id)
    except (ValueError, TypeError):
        return None

def get_effective_chat_id(update: Update) -> Optional[int]:
    """Extracts integer chat_id from update if present."""
    if not update:
        return None
    chat = update.effective_chat
    if not chat:
        return None
    try:
        return int(chat.id)
    except (ValueError, TypeError):
        return None

def is_owner(update: Update) -> bool:
    """Returns True if caller is strictly the primary bot owner (TELEGRAM_USER_ID as int)."""
    user_id = get_effective_user_id(update)
    owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
    if user_id is None or owner_id is None:
        return False
    try:
        return user_id == int(owner_id)
    except (ValueError, TypeError):
        return False

def is_authorized_user(update: Update) -> bool:
    """
    Returns True if caller is the bot owner OR the message originates in the configured group.
    """
    if is_owner(update):
        return True
    chat_id = get_effective_chat_id(update)
    group_id = getattr(config, 'TELEGRAM_GROUP_ID', None)
    if chat_id is not None and group_id is not None:
        try:
            return chat_id == int(group_id)
        except (ValueError, TypeError):
            return False
    return False

async def require_authorized(update: Update) -> bool:
    """
    Ensures the user/chat has basic read-only or owner authorization.
    Sends friendly refusal on unauthorized access and returns False.
    """
    if is_authorized_user(update):
        return True

    user_id = get_effective_user_id(update)
    chat_id = get_effective_chat_id(update)
    logger.warning(f"Unauthorized access rejected for user={user_id}, chat={chat_id}")

    if update.callback_query:
        try:
            await update.callback_query.answer("❌ Unauthorized access.", show_alert=True)
        except Exception:
            pass
    elif update.effective_message:
        try:
            await update.effective_message.reply_text("❌ Unauthorized user.")
        except Exception:
            pass
    return False

async def require_admin(update: Update) -> bool:
    """
    Ensures the caller is strictly the primary bot owner (TELEGRAM_USER_ID).
    Group members and strangers are rejected with a clear admin-only notice.
    """
    if is_owner(update):
        return True

    user_id = get_effective_user_id(update)
    chat_id = get_effective_chat_id(update)
    logger.warning(f"Admin-only action blocked for non-owner user={user_id}, chat={chat_id}")

    refusal_text = "⛔ <b>Admin Only:</b> This action (mutation/settings/export/dashboard) is restricted to the bot owner."
    if update.callback_query:
        try:
            await update.callback_query.answer("⛔ Admin Only: This action is restricted to the bot owner.", show_alert=True)
        except Exception:
            pass
    elif update.effective_message:
        try:
            await update.effective_message.reply_text(refusal_text, parse_mode='HTML')
        except Exception:
            pass
    return False

def get_command_policy(command: str) -> Optional[str]:
    """Returns 'admin', 'read_only', or None for unknown commands."""
    cmd = command.lower().lstrip('/')
    if cmd in ADMIN_COMMANDS:
        return 'admin'
    if cmd in READ_ONLY_COMMANDS:
        return 'read_only'
    return None

def get_callback_policy(action: str) -> Optional[str]:
    """Returns 'admin', 'read_only', or None for unknown callback actions."""
    if action in ADMIN_CALLBACK_ACTIONS:
        return 'admin'
    if action in READ_ONLY_CALLBACK_ACTIONS:
        return 'read_only'
    return None
