"""
Central Authorization and Access Control Module.
Enforces multi-tenant workspace role-based access control (RBAC: owner, admin, member, viewer)
with backward compatibility for single-user deployments.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime
import html
from telegram import Update
from telegram.ext import ContextTypes
import config
from config import logger
from database.models import Workspace, WorkspaceMember

# Role Hierarchy: Higher integer = higher permission level
WORKSPACE_ROLE_HIERARCHY: Dict[str, int] = {
    'owner': 4,
    'admin': 3,
    'member': 2,
    'viewer': 1
}

# Command Policies: Explicit mapping of every bot command to its access policy
READ_ONLY_COMMANDS = {
    "start", "balance", "today", "history", "last5", "recent", "details", "ids",
    "date", "search", "find", "amount", "amt", "monthly", "stats", "filter",
    "sort", "chatid", "help", "menu", "cafeteria", "canteen", "cafestats",
    "cafespends", "budget", "digest", "status"
}

ADMIN_COMMANDS = {
    "edit", "delete", "setbalance", "export", "report", "statement",
    "restore", "importbackup", "backup", "backupnow", "undo", "revert", "setbudget", "addmenu", "delmenu",
    "cafeedit", "editcafe", "dashboard", "setmodel", "model",
    "gemini", "geministatus", "quota", "ai", "insights"
}

# Command to Minimum Role Level Policy
COMMAND_ROLE_POLICY = {
    # Viewer & above (Read-Only)
    "start": "viewer", "balance": "viewer", "today": "viewer", "history": "viewer",
    "last5": "viewer", "recent": "viewer", "details": "viewer", "ids": "viewer",
    "date": "viewer", "search": "viewer", "find": "viewer", "amount": "viewer",
    "amt": "viewer", "monthly": "viewer", "stats": "viewer", "filter": "viewer",
    "sort": "viewer", "chatid": "viewer", "help": "viewer", "menu": "viewer",
    "cafeteria": "viewer", "canteen": "viewer", "cafestats": "viewer",
    "cafespends": "viewer", "budget": "viewer", "digest": "viewer",
    "status": "viewer", "members": "viewer", "workspace": "viewer", "workspaces": "viewer",
    "join": "viewer",

    # Member & above (Mutation: Logging Payments, Self-Edit/Delete, Self-Undo & Reports)
    "log_receipt": "member", "log_text": "member", "cafe_tag": "member",
    "quick_add": "member", "self_undo": "member",
    "edit": "member", "delete": "member", "undo": "member", "revert": "member",
    "export": "member", "report": "member", "statement": "member",
    "dashboard": "member",

    # Admin & above (Management & Mutations, AI Quota & Insights)
    "setbudget": "admin", "addmenu": "admin",
    "delmenu": "admin", "cafeedit": "admin", "editcafe": "admin",
    "backup": "admin", "backupnow": "admin",
    "invite": "admin", "invite_member": "admin", "audit": "admin",
    "setmodel": "admin", "model": "admin",
    "gemini": "admin", "geministatus": "admin", "quota": "admin", "ai": "admin", "insights": "admin",

    # Owner only (Governance, Initial Balance, Permissions & Disaster Recovery)
    "setbalance": "owner", "restore": "owner", "importbackup": "owner",
    "setrole": "owner", "permissions": "owner", "roles": "owner",
    "workspace_settings": "owner", "transfer_ownership": "owner"
}

# Callback Action Prefix Policies
READ_ONLY_CALLBACK_ACTIONS = {
    "nav", "filter", "sort", "cafe_stats", "cafe_view_menu", "tx_view",
    "ws_switch", "ws_reset", "ws_reset_menu", "ws_new_prompt", "perm_view", "perm_list"
}

ADMIN_CALLBACK_ACTIONS = {
    # AI & Model Controls (Admin Only)
    "refresh_gemini", "set_model",
    # Access Approval & Permissions (Admin/Owner Only)
    "auth_grant", "auth_deny", "perm_set", "perm_remove", "perm_remove_confirm",
    # Receipt Card Actions & Pending Edits
    "save_p", "force_save_p", "edit_p", "ep_field", "ep_back", "cat_p", "set_pcat", "cancel_p",
    # Undo & Quick Add & Duplicate
    "undo_tx", "quick_add", "undo_action", "undo_confirm", "undo_cancel", "dup_tx", "qa_payee",
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
    "restore_confirm", "restore_cancel", "backup_now",
    # JSON Upload Import Confirmation
    "json_import_confirm", "json_import_cancel",
    # Recurring & Monthly Reviews
    "rec_paid", "rec_skip", "rec_pause", "rec_resume", "rec_del", "close_month",
    # Gemini Model Switching
    "set_model"
}

# Fine-grained Callback Role Policy
CALLBACK_ROLE_POLICY = {
    # Viewer & above
    "nav": "viewer", "filter": "viewer", "sort": "viewer",
    "cafe_stats": "viewer", "cafe_view_menu": "viewer", "tx_view": "viewer",
    "refresh_gemini": "viewer",
    "ws_switch": "viewer", "ws_reset": "viewer", "ws_reset_menu": "viewer",
    "ws_new_prompt": "viewer", "perm_view": "viewer", "perm_list": "viewer",

    # Member & above
    "save_p": "member", "force_save_p": "member", "edit_p": "member",
    "ep_field": "member", "ep_back": "member", "cat_p": "member",
    "set_pcat": "member", "cancel_p": "member", "quick_add": "member",
    "undo_tx": "member", "undo_action": "member", "undo_confirm": "member",
    "undo_cancel": "member", "dup_tx": "member", "qa_payee": "member",
    "confirm_tx": "member", "cancel_tx": "member", "cafe_pick": "member",
    "cafe_mode": "member", "cafe_custom_prompt": "member", "cafe_cart_add": "member",
    "cafe_cart_clear": "member", "cafe_cart_done": "member", "cafe_cat": "member",
    "cafe_back": "member", "cafe_addon": "member", "cafe_edit_last": "member",
    "cafe_skip": "member",

    # Member & above (self-service edit/delete with ownership check)
    "select_edit": "member", "select_edit_cancel": "member",
    "select_delete": "member", "select_delete_cancel": "member",
    "edit_field": "member", "edit_cancel": "member",
    "delete_confirm": "member", "delete_cancel": "member", "correct_amount": "member",
    "edit_tx": "member", "delete_tx": "member", "export_file": "member",

    # Admin & above
    "cafe_edit": "admin", "cafe_menu_add_prompt": "admin", "cafe_menu_del_prompt": "admin",
    "cafe_del_item": "admin", "cafe_del_cancel": "admin", "backup_now": "admin",
    "rec_paid": "admin", "rec_skip": "admin", "rec_pause": "admin",
    "rec_resume": "admin", "rec_del": "admin", "close_month": "admin", "set_model": "admin",
    "perm_remove": "admin", "perm_remove_confirm": "admin",

    # Owner only
    "restore_confirm": "owner", "restore_cancel": "owner",
    "json_import_confirm": "owner", "json_import_cancel": "owner"
}

WORKSPACE_CALLBACK_POLICY = CALLBACK_ROLE_POLICY


from typing import Optional, Dict, Any

@dataclass
class RequestContext:
    """Carries resolved tenant workspace and membership identity for an incoming Telegram interaction."""
    workspace_id: str = ""
    chat_id: int = 0
    chat_type: str = "private"
    user_id: int = 0
    username: str = ""
    display_name: str = ""
    role: str = "member"  # 'owner', 'admin', 'member', 'viewer'
    workspace: Optional[Any] = None
    member: Optional[Any] = None
    telegram_user_id: Optional[int] = None

    def __post_init__(self):
        if self.telegram_user_id is not None and not self.user_id:
            self.user_id = int(self.telegram_user_id)
        elif self.user_id and self.telegram_user_id is None:
            self.telegram_user_id = int(self.user_id)

    def has_role(self, required_role: str) -> bool:
        """Evaluates whether caller meets or exceeds the required role level."""
        caller_level = WORKSPACE_ROLE_HIERARCHY.get(self.role, 0)
        req_level = WORKSPACE_ROLE_HIERARCHY.get(required_role, 2)
        return caller_level >= req_level


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


def is_super_admin(user_id: Optional[int]) -> bool:
    """Returns True if user_id is in configured emergency SUPER_ADMIN_USER_IDS."""
    if user_id is None:
        return False
    super_admins = getattr(config, 'SUPER_ADMIN_USER_IDS', [])
    try:
        return int(user_id) in super_admins
    except (ValueError, TypeError):
        return False


def is_owner(update: Update, workspace_id: Optional[str] = None) -> bool:
    """
    Returns True if caller is emergency SUPER_ADMIN, owner of the workspace in workspace_members,
    or legacy TELEGRAM_USER_ID if LEGACY_SINGLE_TENANT_MODE is True.
    """
    user_id = get_effective_user_id(update)
    if user_id is None:
        return False
    # Nagendra (8343764796) is strictly a member only, never owner
    if int(user_id) == 8343764796:
        return False
    if is_super_admin(user_id):
        return True

    # Global bot owner (configured via TELEGRAM_USER_ID) always has owner privileges
    owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
    if owner_id is not None:
        try:
            if int(user_id) == int(owner_id):
                return True
        except (ValueError, TypeError):
            pass

    from database.queries import get_workspace_member, get_workspace_by_chat_id, get_default_workspace_id
    chat_id = get_effective_chat_id(update)
    ws_id = workspace_id or get_user_active_workspace(user_id)
    if not ws_id and chat_id is not None:
        ws = get_workspace_by_chat_id(chat_id)
        if ws:
            ws_id = ws.id
        else:
            ws_id = get_default_workspace_id()
    if ws_id:
        member = get_workspace_member(ws_id, user_id)
        if member and member.role == 'owner' and member.is_active and getattr(member, 'status', 'active') == 'active':
            return True
    return False


def is_admin_or_owner(update: Update, workspace_id: Optional[str] = None) -> bool:
    """Returns True if caller is emergency super admin, global bot owner, workspace owner, or workspace admin."""
    if is_owner(update, workspace_id=workspace_id):
        return True
    user_id = get_effective_user_id(update)
    if user_id is None:
        return False
    from database.queries import get_workspace_by_chat_id, get_workspace_member, get_default_workspace_id
    chat_id = get_effective_chat_id(update)
    chat = getattr(update, 'effective_chat', None)
    chat_type = getattr(chat, 'type', 'private') if chat else 'private'
    ws_id = workspace_id or (get_user_active_workspace(user_id) if chat_type == 'private' else None)
    if not ws_id and chat_id is not None:
        ws = get_workspace_by_chat_id(chat_id)
        if ws:
            ws_id = ws.id
        else:
            ws_id = get_default_workspace_id()
    if ws_id:
        member = get_workspace_member(ws_id, user_id)
        if member and member.role in ('admin', 'owner') and member.is_active and getattr(member, 'status', 'active') == 'active':
            return True
    return False

is_admin = is_admin_or_owner


def is_authorized_user(update: Update, workspace_id: Optional[str] = None) -> bool:
    """
    Returns True if caller has active membership in the target/current workspace,
    or is emergency SUPER_ADMIN. Group membership or chat_id alone does not grant access.
    """
    user_id = get_effective_user_id(update)
    chat_id = get_effective_chat_id(update)
    if user_id is None or chat_id is None:
        return False

    if is_super_admin(user_id):
        return True

    if is_owner(update, workspace_id=workspace_id):
        return True

    if getattr(config, 'LEGACY_SINGLE_TENANT_MODE', False) or getattr(config, 'WORKSPACE_MIGRATION_COMPATIBILITY', False):
        group_id = getattr(config, 'TELEGRAM_GROUP_ID', None)
        if group_id is not None:
            try:
                if chat_id == int(group_id):
                    return True
            except (ValueError, TypeError):
                pass

    # Multi-tenant mode: Check membership in current workspace
    from database.queries import get_workspace_by_chat_id, get_workspace_member
    chat = getattr(update, 'effective_chat', None)
    chat_type = getattr(chat, 'type', 'private') if chat else 'private'
    ws_id = workspace_id or (get_user_active_workspace(user_id) if chat_type == 'private' else None)
    if not ws_id:
        ws = get_workspace_by_chat_id(chat_id)
        if ws:
            ws_id = ws.id

    if ws_id:
        member = get_workspace_member(ws_id, user_id)
        if member and member.is_active and getattr(member, 'status', 'active') == 'active':
            return True

    return False



_USER_ACTIVE_WORKSPACES: Dict[int, str] = {}

def get_user_active_workspace(user_id: int) -> Optional[str]:
    """Retrieves the active workspace ID override for a user in private DM."""
    if not user_id:
        return None
    from database.queries import get_workspace_setting, get_default_workspace_id, get_workspace_by_id
    ws_id = _USER_ACTIVE_WORKSPACES.get(user_id)
    if ws_id:
        # Verify cached workspace still exists in DB
        if get_workspace_by_id(str(ws_id)):
            return ws_id
        _USER_ACTIVE_WORKSPACES.pop(user_id, None)

    try:
        val = get_workspace_setting(get_default_workspace_id(), f"user_active_ws:{user_id}")
        if val:
            if get_workspace_by_id(str(val)):
                _USER_ACTIVE_WORKSPACES[user_id] = str(val)
                return str(val)
            else:
                # Stale or deleted workspace reference: clean up immediately
                _USER_ACTIVE_WORKSPACES.pop(user_id, None)
                set_user_active_workspace(user_id, None)
    except Exception:
        pass
    return None

def set_user_active_workspace(user_id: int, workspace_id: Optional[str]):
    """Sets or clears the active workspace ID override for a user in private DM."""
    if not user_id:
        return
    try:
        from database.queries import set_workspace_setting, get_default_workspace_id
        def_id = get_default_workspace_id()
        if workspace_id:
            _USER_ACTIVE_WORKSPACES[user_id] = str(workspace_id)
            set_workspace_setting(def_id, f"user_active_ws:{user_id}", str(workspace_id))
        else:
            _USER_ACTIVE_WORKSPACES.pop(user_id, None)
            set_workspace_setting(def_id, f"user_active_ws:{user_id}", "")
    except Exception as e:
        logger.error(f"Failed to set user active workspace: {e}")


def get_workspace_context(update: Update) -> Optional[RequestContext]:
    """
    Extracts and resolves workspace context and membership for an Update synchronously.
    Handles auto-provisioning for private chats and group chats.
    """
    if not update:
        return None
    user_id = get_effective_user_id(update)
    chat_id = get_effective_chat_id(update)
    if user_id is None or chat_id is None:
        return None

    chat = update.effective_chat
    user = update.effective_user
    raw_type = getattr(chat, 'type', 'private')
    chat_type = str(raw_type) if isinstance(raw_type, str) else 'private'
    raw_title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or "Workspace"
    chat_title = str(raw_title) if isinstance(raw_title, str) else "Workspace"
    raw_uname = getattr(user, 'username', '')
    username = str(raw_uname) if isinstance(raw_uname, str) else ''
    raw_name = getattr(user, 'full_name', None) or getattr(user, 'first_name', None) or username or str(user_id)
    display_name = str(raw_name) if isinstance(raw_name, str) else str(user_id)

    from database.queries import (
        get_workspace_by_chat_id, get_or_create_workspace,
        get_workspace_member, add_workspace_member, get_all_workspace_members,
        get_workspace_setting, get_workspace_by_id, get_default_workspace_id
    )

    try:
        is_global_owner = False
        owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
        if owner_id is not None:
            try:
                is_global_owner = (int(user_id) == int(owner_id))
            except (ValueError, TypeError):
                pass

        # Check if user has switched active workspace (applies to private DMs and group chats)
        active_ws_id = get_user_active_workspace(user_id)
        if active_ws_id:
            switched_ws = get_workspace_by_id(active_ws_id)
            if switched_ws:
                member = get_workspace_member(switched_ws.id, user_id)
                if is_super_admin(user_id) or is_global_owner or member:
                    caller_role = 'owner' if (is_super_admin(user_id) or is_global_owner) else (member.role if member else 'member')
                    if user_id == 8343764796 and caller_role == 'owner':
                        caller_role = 'member'
                    return RequestContext(
                        workspace_id=switched_ws.id,
                        chat_id=chat_id,
                        chat_type=chat_type,
                        user_id=user_id,
                        username=username,
                        display_name=display_name,
                        role=caller_role,
                        workspace=switched_ws,
                        member=member,
                        telegram_user_id=user_id
                    )

        ws = None
        # User requested: for Pranav (owner), in private DMs, use Payment (Group) directly
        if chat_type == 'private' and is_global_owner:
            def_ws_id = get_default_workspace_id()
            if def_ws_id:
                ws = get_workspace_by_id(def_ws_id)

        if ws is None:
            ws = get_workspace_by_chat_id(chat_id)

        if ws is None:
            if not is_authorized_user(update):
                return None
            creator_id = user_id
            if getattr(config, 'LEGACY_SINGLE_TENANT_MODE', False) or is_global_owner:
                legacy_owner = getattr(config, 'TELEGRAM_USER_ID', None)
                if legacy_owner:
                    try:
                        creator_id = int(legacy_owner)
                    except (ValueError, TypeError):
                        pass
            ws = get_or_create_workspace(
                chat_id,
                chat_type=chat_type,
                title=chat_title,
                creator_user_id=creator_id,
                username=username,
                display_name=display_name
            )

        member = get_workspace_member(ws.id, user_id)
        if member is None:
            if is_super_admin(user_id):
                caller_role = 'owner'
            elif getattr(config, 'LEGACY_SINGLE_TENANT_MODE', False) or is_global_owner:
                owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
                if owner_id is not None and user_id == int(owner_id):
                    caller_role = 'owner'
                elif chat_type in ('group', 'supergroup'):
                    caller_role = 'viewer'
                else:
                    return None
            elif chat_type in ('group', 'supergroup'):
                default_role = get_workspace_setting(ws.id, 'default_member_role') or getattr(config, 'DEFAULT_MEMBER_ROLE', 'member')
                member = add_workspace_member(
                    ws.id, user_id,
                    username=username,
                    display_name=display_name,
                    role=default_role
                )
                caller_role = member.role if member else default_role
            elif chat_type == 'private' and (getattr(config, 'ALLOW_PUBLIC_WORKSPACES', False) or getattr(config, 'ALLOW_PUBLIC_WORKSPACE_CREATION', False)):
                member = add_workspace_member(
                    ws.id, user_id,
                    username=username,
                    display_name=display_name,
                    role='member' if user_id == 8343764796 else 'owner'
                )
                caller_role = 'member' if user_id == 8343764796 else 'owner'
            else:
                return None
        else:
            if not member.is_active or getattr(member, 'status', 'active') in ('suspended', 'removed'):
                return None
            caller_role = member.role
            if is_super_admin(user_id):
                caller_role = 'owner'
            elif getattr(config, 'LEGACY_SINGLE_TENANT_MODE', False) or is_global_owner:
                owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
                if owner_id is not None and user_id == int(owner_id):
                    caller_role = 'owner'

        if user_id == 8343764796 and caller_role == 'owner':
            caller_role = 'member'


        return RequestContext(
            workspace_id=ws.id,
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            username=username,
            display_name=display_name,
            role=caller_role,
            workspace=ws,
            member=member
        )
    except Exception as e:
        logger.error(f"Error resolving workspace context for user={user_id}, chat={chat_id}: {e}")
        return None


async def resolve_workspace_context(
    update: Update,
    required_policy: Optional[str] = None
) -> Optional[RequestContext]:
    """
    Asynchronously resolves workspace context and enforces role policy.
    If unauthorized or role is insufficient, replies with a user-friendly notice and returns None.
    """
    if not is_authorized_user(update):
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
        return None

    ctx = get_workspace_context(update)
    if not ctx:
        if update.callback_query:
            try:
                await update.callback_query.answer("⛔ Access denied.", show_alert=True)
            except Exception:
                pass
        return None

    if required_policy is not None:
        target_role = COMMAND_ROLE_POLICY.get(required_policy, required_policy)
        if target_role == "read_only":
            target_role = "viewer"
        req_level = WORKSPACE_ROLE_HIERARCHY.get(target_role, 1 if required_policy == "read_only" else 2)
        caller_level = WORKSPACE_ROLE_HIERARCHY.get(ctx.role, 0)
        if caller_level < req_level:
            from utils.telemetry import increment_metric
            increment_metric("auth_denials")
            logger.warning(
                f"Access denied for user={ctx.user_id} (role={ctx.role}) in workspace={ctx.workspace_id}: "
                f"requires {target_role} (level {req_level})"
            )
            refusal_text = (
                f"⛔ <b>Access Restricted:</b> This action requires "
                f"<code>{target_role.upper()}</code> role in this workspace.\n"
                f"<i>Your current role: <b>{ctx.role.title()}</b></i>"
            )
            msg_target = getattr(update, 'effective_message', None) or getattr(update, 'message', None)
            if update.callback_query:
                try:
                    await update.callback_query.answer(
                        f"⛔ Restricted: Requires {target_role} role.",
                        show_alert=True
                    )
                except Exception:
                    pass
            elif msg_target:
                try:
                    await msg_target.reply_text(refusal_text, parse_mode='HTML')
                except Exception:
                    pass
            return None

    return ctx

# Alias for concise import
resolve_context = resolve_workspace_context


async def require_authorized(update: Update, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> bool:
    """
    Ensures the user/chat has basic read-only or workspace authorization.
    If unauthorized:
    - If status is 'pending', notifies user that access request is pending approval.
    - If status is 'rejected' or user is deactivated, notifies user that access is denied.
    - If new user, creates an access request in database and sends approval request with inline buttons to the bot owner.
    """
    if is_authorized_user(update):
        return True

    user_id = get_effective_user_id(update)
    chat_id = get_effective_chat_id(update)
    if not user_id or not chat_id:
        return False

    from database.queries import get_access_request, create_access_request, get_workspace_member, get_workspace_by_chat_id
    from utils.telemetry import increment_metric
    increment_metric("auth_denials")
    logger.warning(f"Unauthorized interaction from user={user_id}, chat={chat_id}")

    user = update.effective_user
    chat = update.effective_chat
    username = getattr(user, 'username', '') or ''
    full_name = getattr(user, 'full_name', '') or getattr(user, 'first_name', '') or username or str(user_id)
    chat_type = getattr(chat, 'type', 'private') or 'private'

    req = get_access_request(user_id)
    status = req.get('status') if req else None

    # Check if user has an explicit deactivated membership in this workspace
    ws = get_workspace_by_chat_id(chat_id)
    member = get_workspace_member(ws.id, user_id) if ws else None
    if member and not member.is_active:
        status = 'rejected'

    if update.callback_query:
        if status == 'rejected':
            try:
                await update.callback_query.answer("⛔ Access Denied. You do not have permission to use this bot.", show_alert=True)
            except Exception:
                pass
        else:
            try:
                await update.callback_query.answer("⏳ Access Request Pending. Awaiting approval from the bot owner.", show_alert=True)
            except Exception:
                pass
        return False

    msg_target = getattr(update, 'effective_message', None) or getattr(update, 'message', None)
    if not msg_target:
        return False

    if status == 'rejected':
        try:
            await msg_target.reply_text(
                "⛔ <b>Access Denied</b>\n\n"
                "You do not have permission to access this bot. Please contact the administrator.",
                parse_mode='HTML'
            )
        except Exception:
            pass
        return False

    if status == 'pending':
        try:
            await msg_target.reply_text(
                "⏳ <b>Access Request Pending</b>\n\n"
                "Your request to access Payment Tracker has been submitted and is awaiting approval from the bot owner.\n\n"
                "<i>You will receive a notification here once approved.</i>",
                parse_mode='HTML'
            )
        except Exception:
            pass
        return False

    # New user: Create access request and notify bot owner
    create_access_request(
        telegram_user_id=user_id,
        username=username,
        display_name=full_name,
        chat_id=chat_id,
        chat_type=chat_type
    )

    try:
        await msg_target.reply_text(
            "⏳ <b>Access Request Submitted</b>\n\n"
            "Welcome! This bot requires owner approval to protect financial privacy.\n\n"
            "Your access request has been sent to the bot owner. You will receive a message here as soon as permission is granted.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to reply to new user {user_id}: {e}")

    # Notify Bot Owner with Interactive Inline Buttons
    owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
    if owner_id:
        try:
            bot = update.get_bot() if hasattr(update, 'get_bot') else getattr(update, '_bot', None)
            if not bot and context:
                bot = context.bot
            if bot:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                owner_msg = (
                    "🔔 <b>New User Access Request</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Name:</b> {html.escape(full_name)}\n"
                    f"💬 <b>Username:</b> @{html.escape(username) if username else 'N/A'}\n"
                    f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
                    f"💬 <b>Chat Type:</b> <code>{chat_type}</code>\n"
                    f"📅 <b>Time:</b> {time_str}\n\n"
                    "<i>Select permission level to grant or deny access:</i>"
                )
                markup = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("👤 Approve Member", callback_data=f"auth_grant:{user_id}:member"),
                        InlineKeyboardButton("🛡️ Approve Admin", callback_data=f"auth_grant:{user_id}:admin")
                    ],
                    [
                        InlineKeyboardButton("👁️ Approve Viewer", callback_data=f"auth_grant:{user_id}:viewer"),
                        InlineKeyboardButton("❌ Reject / Block", callback_data=f"auth_deny:{user_id}")
                    ]
                ])
                await bot.send_message(
                    chat_id=int(owner_id),
                    text=owner_msg,
                    reply_markup=markup,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"Failed to notify owner {owner_id} of access request: {e}", exc_info=True)

    return False


async def require_member(update: Update, silent: bool = False) -> bool:
    """
    Ensures the caller has at least 'member' role in the current workspace (member, admin, or owner).
    Viewers and strangers are rejected with a clear role requirement notice.
    """
    if is_owner(update):
        return True

    chat_id = get_effective_chat_id(update)
    user_id = get_effective_user_id(update)
    if user_id is not None:
        from database.queries import get_workspace_by_chat_id, get_workspace_member
        chat = getattr(update, 'effective_chat', None)
        chat_type = getattr(chat, 'type', 'private') if chat else 'private'
        ws_id = get_user_active_workspace(user_id) if chat_type == 'private' else None
        if not ws_id and chat_id is not None:
            ws = get_workspace_by_chat_id(chat_id)
            if ws:
                ws_id = ws.id
        if ws_id:
            member = get_workspace_member(ws_id, user_id)
            if member and member.role in ('member', 'admin', 'owner') and member.is_active and getattr(member, 'status', 'active') == 'active':
                return True

    if silent:
        return False

    from utils.telemetry import increment_metric
    increment_metric("auth_denials")
    logger.warning(f"Payment action blocked for non-member user={user_id}, chat={chat_id}")

    refusal_text = "⛔ <b>Member Only:</b> Logging or managing payments is restricted to <code>MEMBER</code> or higher role in this workspace."
    msg_target = getattr(update, 'effective_message', None) or getattr(update, 'message', None)
    if update.callback_query:
        try:
            await update.callback_query.answer("⛔ Restricted: Requires Member role.", show_alert=True)
        except Exception:
            pass
    elif msg_target:
        try:
            await msg_target.reply_text(refusal_text, parse_mode='HTML')
        except Exception:
            pass
    return False


async def require_admin(update: Update, silent: bool = False) -> bool:
    """
    Ensures the caller has administrative role in current workspace or is global bot owner.
    Non-admin members and strangers are rejected with a clear admin-only notice.
    """
    if is_owner(update):
        return True

    chat_id = get_effective_chat_id(update)
    user_id = get_effective_user_id(update)
    if user_id is not None:
        from database.queries import get_workspace_by_chat_id, get_workspace_member
        chat = getattr(update, 'effective_chat', None)
        chat_type = getattr(chat, 'type', 'private') if chat else 'private'
        ws_id = get_user_active_workspace(user_id) if chat_type == 'private' else None
        if not ws_id and chat_id is not None:
            ws = get_workspace_by_chat_id(chat_id)
            if ws:
                ws_id = ws.id
        if ws_id:
            member = get_workspace_member(ws_id, user_id)
            if member and member.role in ('admin', 'owner') and member.is_active and getattr(member, 'status', 'active') == 'active':
                return True

    if silent:
        return False

    from utils.telemetry import increment_metric
    increment_metric("auth_denials")
    logger.warning(f"Admin-only action blocked for non-owner user={user_id}, chat={chat_id}")

    refusal_text = "⛔ <b>Admin Only:</b> This action (mutation/settings/export/dashboard) is restricted to administrators."
    msg_target = getattr(update, 'effective_message', None) or getattr(update, 'message', None)
    if update.callback_query:
        try:
            await update.callback_query.answer("⛔ Admin Only: This action requires Administrator role.", show_alert=True)
        except Exception:
            pass
    elif msg_target:
        try:
            await msg_target.reply_text(refusal_text, parse_mode='HTML')
        except Exception:
            pass
    return False


async def require_owner(update: Update, silent: bool = False) -> bool:
    """
    Ensures the caller is the global bot owner or workspace owner.
    """
    if is_owner(update):
        return True

    user_id = get_effective_user_id(update)
    chat_id = get_effective_chat_id(update)

    if silent:
        return False

    from utils.telemetry import increment_metric
    increment_metric("auth_denials")
    logger.warning(f"Owner-only action blocked for user={user_id}, chat={chat_id}")

    refusal_text = "⛔ <b>Owner Only:</b> Access restricted — this command is strictly reserved for the Bot Owner."
    msg_target = getattr(update, 'effective_message', None) or getattr(update, 'message', None)
    if update.callback_query:
        try:
            await update.callback_query.answer("⛔ Owner Only: Strictly reserved for Bot Owner.", show_alert=True)
        except Exception:
            pass
    elif msg_target:
        try:
            await msg_target.reply_text(refusal_text, parse_mode='HTML')
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
    """Returns 'admin', 'read_only', or role from WORKSPACE_CALLBACK_POLICY."""
    if action in ADMIN_CALLBACK_ACTIONS:
        return 'admin'
    if action in READ_ONLY_CALLBACK_ACTIONS:
        return 'read_only'
    if action in WORKSPACE_CALLBACK_POLICY:
        return WORKSPACE_CALLBACK_POLICY[action]
    return None
