"""
Workspace Invite Service.
Generates, validates, and redeems cryptographically secure hashed invitation tokens for workspaces.
"""

import uuid
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
from database.db import get_db_connection, LEDGER_LOCK
from config import logger


def _hash_token(raw_token: str) -> str:
    """Computes SHA-256 hash of raw invitation token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def create_workspace_invite(
    workspace_id: str,
    creator_user_id: int,
    intended_role: str = "member",
    max_uses: int = 1,
    expiry_hours: int = 24
) -> Tuple[Optional[str], Optional[str]]:
    """
    Creates an invitation token.
    Returns (raw_token, invite_id) or (None, None) on error.
    The raw_token is ONLY returned once to the caller and NEVER stored in plaintext.
    """
    if not workspace_id:
        return None, None

    if intended_role not in ("admin", "member", "viewer"):
        intended_role = "member"

    raw_token = secrets.token_urlsafe(24)
    token_hash = _hash_token(raw_token)
    invite_id = str(uuid.uuid4())

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=max(1, expiry_hours))).isoformat()
    now_utc = now.isoformat()

    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO workspace_invites (
                        id, workspace_id, token_hash, created_by,
                        intended_role, max_uses, uses_count,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (
                    invite_id, str(workspace_id), token_hash, int(creator_user_id),
                    intended_role, max(1, max_uses), expires_at, now_utc
                ))
                conn.commit()

        from database.queries import get_workspace_member
        from config import SUPER_ADMIN_IDS
        cm = get_workspace_member(workspace_id, creator_user_id)
        c_role = cm.role if cm else ("owner" if int(creator_user_id) in SUPER_ADMIN_IDS else "admin")
        from services.audit_service import log_audit_event
        log_audit_event(
            workspace_id=workspace_id,
            actor_user_id=creator_user_id,
            actor_role=c_role,
            action="invite_created",
            resource=f"invite:{invite_id}",
            details={"intended_role": intended_role, "max_uses": max_uses, "expires_at": expires_at}
        )
        return raw_token, invite_id
    except Exception as e:
        logger.error(f"Failed to create invite in workspace={workspace_id}: {e}")
        return None, None


def revoke_workspace_invite(workspace_id: str, invite_id: str, revoked_by: int) -> bool:
    """
    Revokes an active invitation.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE workspace_invites
                    SET revoked_at = ?
                    WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL
                """, (now_utc, invite_id, workspace_id))
                updated = cursor.rowcount > 0
                conn.commit()

        if updated:
            from database.queries import get_workspace_member
            from config import SUPER_ADMIN_IDS
            rm = get_workspace_member(workspace_id, revoked_by)
            r_role = rm.role if rm else ("owner" if int(revoked_by) in SUPER_ADMIN_IDS else "admin")
            from services.audit_service import log_audit_event
            log_audit_event(
                workspace_id=workspace_id,
                actor_user_id=revoked_by,
                actor_role=r_role,
                action="invite_revoked",
                resource=f"invite:{invite_id}"
            )
        return updated
    except Exception as e:
        logger.error(f"Failed to revoke invite={invite_id}: {e}")
        return False


def validate_and_redeem_invite(
    raw_token: str,
    user_id: int,
    username: str = "",
    display_name: str = ""
) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """
    Validates a raw invite token and redeems it if valid.
    Returns: (success, message, workspace_id, role_granted)
    """
    if not raw_token or not raw_token.strip():
        return False, "❌ Invalid or empty invite token.", None, None

    token_hash = _hash_token(raw_token)
    now_utc = datetime.now(timezone.utc).isoformat()

    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT i.id, i.workspace_id, i.intended_role, i.max_uses,
                       i.uses_count, i.expires_at, i.revoked_at,
                       w.title, w.is_active, w.status
                FROM workspace_invites i
                JOIN workspaces w ON i.workspace_id = w.id
                WHERE i.token_hash = ?
            """, (token_hash,))
            row = cursor.fetchone()

            if not row:
                return False, "❌ Invite not found or token is invalid.", None, None

            invite_id = row['id']
            ws_id = row['workspace_id']
            role = row['intended_role']
            max_uses = row['max_uses']
            uses_count = row['uses_count']
            expires_at = row['expires_at']
            revoked_at = row['revoked_at']
            ws_title = row['title'] or "Workspace"
            ws_is_active = bool(row['is_active'])
            ws_status = row['status'] or 'active'

            if not ws_is_active or ws_status != 'active':
                return False, "⛔ The workspace associated with this invite is suspended or inactive.", None, None

            if revoked_at:
                return False, "❌ This invite has been revoked by an administrator.", None, None

            if now_utc > expires_at:
                return False, "⏳ This invite token has expired.", None, None

            if uses_count >= max_uses:
                return False, "❌ This invite has reached its maximum usage limit.", None, None

            # Check if user is banned or previously removed
            cursor.execute("""
                SELECT value FROM workspace_settings
                WHERE workspace_id = ? AND (key = ? OR key = ?)
            """, (ws_id, f"banned_user:{int(user_id)}", f"removed_user:{int(user_id)}"))
            if cursor.fetchone():
                return False, "⛔ You are banned or removed from this workspace and cannot rejoin via invite link.", None, None

            # Check if user is already an active member before consuming
            cursor.execute("""
                SELECT role, is_active, status FROM workspace_members
                WHERE workspace_id = ? AND telegram_user_id = ?
            """, (ws_id, int(user_id)))
            mem_row = cursor.fetchone()

            if mem_row:
                if mem_row['is_active'] and mem_row['status'] == 'active':
                    return False, f"ℹ️ You are already an active member of <b>{ws_title}</b>.", ws_id, mem_row['role']
                if mem_row['status'] in ('suspended', 'removed'):
                    return False, "⛔ You were removed or suspended from this workspace and cannot rejoin via invite link.", None, None

            # Atomic conditional consumption of invite
            cursor.execute("""
                UPDATE workspace_invites
                SET uses_count = uses_count + 1
                WHERE id = ? AND uses_count < max_uses AND revoked_at IS NULL AND ? <= expires_at
            """, (invite_id, now_utc))
            if cursor.rowcount != 1:
                return False, "❌ This invite has reached its maximum usage limit, expired, or was revoked.", None, None

            if mem_row:
                # Reactivate suspended/inactive member with the invite's role
                cursor.execute("""
                    UPDATE workspace_members
                    SET role = ?, is_active = 1, status = 'active', updated_at = ?
                    WHERE workspace_id = ? AND telegram_user_id = ?
                """, (role, now_utc, ws_id, int(user_id)))
            else:
                # Add new active member
                cursor.execute("""
                    INSERT INTO workspace_members (
                        workspace_id, telegram_user_id, username, display_name,
                        role, is_active, status, joined_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 'active', ?, ?)
                """, (ws_id, int(user_id), username, display_name or str(user_id), role, now_utc, now_utc))

            conn.commit()

    from services.audit_service import log_audit_event
    log_audit_event(
        workspace_id=ws_id,
        actor_user_id=user_id,
        actor_role=role,
        action="invite_redeemed",
        resource=f"invite:{invite_id}",
        details={"granted_role": role, "new_member_id": user_id}
    )

    return True, f"🎉 Successfully joined <b>{ws_title}</b> as <b>{role.title()}</b>!", ws_id, role


# Alias for convenience
redeem_workspace_invite = validate_and_redeem_invite


def is_user_banned_from_workspace(workspace_id: str, telegram_user_id: int | str) -> bool:
    """Checks if a user is banned or suspended/removed from a workspace."""
    if not workspace_id or not telegram_user_id:
        return False
    uid = int(telegram_user_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT value FROM workspace_settings
            WHERE workspace_id = ? AND (key = ? OR key = ?)
        """, (str(workspace_id), f"banned_user:{uid}", f"removed_user:{uid}"))
        if cursor.fetchone():
            return True
        cursor.execute("""
            SELECT status FROM workspace_members
            WHERE workspace_id = ? AND telegram_user_id = ?
        """, (str(workspace_id), uid))
        row = cursor.fetchone()
        if row and row['status'] in ('suspended', 'removed'):
            return True
    return False


def ban_workspace_user(workspace_id: str, telegram_user_id: int | str, banned_by: int | str | None = None) -> bool:
    """Bans a user from a workspace, updating workspace_settings and member status."""
    if not workspace_id or not telegram_user_id:
        return False
    uid = int(telegram_user_id)
    now_utc = datetime.now(timezone.utc).isoformat()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at)
                VALUES (?, ?, '1', ?)
            """, (str(workspace_id), f"banned_user:{uid}", now_utc))
            cursor.execute("""
                UPDATE workspace_members
                SET is_active = 0, status = 'suspended', updated_at = ?
                WHERE workspace_id = ? AND telegram_user_id = ?
            """, (now_utc, str(workspace_id), uid))
            conn.commit()

    if banned_by:
        from database.queries import get_workspace_member
        from config import SUPER_ADMIN_IDS
        bm = get_workspace_member(str(workspace_id), int(banned_by))
        b_role = bm.role if bm else ("owner" if int(banned_by) in SUPER_ADMIN_IDS else "admin")
        from services.audit_service import log_audit_event
        log_audit_event(
            workspace_id=str(workspace_id),
            actor_user_id=int(banned_by),
            actor_role=b_role,
            action="member_banned",
            resource=f"user:{uid}",
            details={"banned_user_id": uid}
        )
    return True


def unban_workspace_user(workspace_id: str, telegram_user_id: int | str, unbanned_by: int | str | None = None) -> bool:
    """Unbans a user from a workspace, removing ban and removed markers from workspace_settings."""
    if not workspace_id or not telegram_user_id:
        return False
    uid = int(telegram_user_id)
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM workspace_settings
                WHERE workspace_id = ? AND (key = ? OR key = ?)
            """, (str(workspace_id), f"banned_user:{uid}", f"removed_user:{uid}"))
            conn.commit()

    if unbanned_by:
        from database.queries import get_workspace_member
        from config import SUPER_ADMIN_IDS
        bm = get_workspace_member(str(workspace_id), int(unbanned_by))
        b_role = bm.role if bm else ("owner" if int(unbanned_by) in SUPER_ADMIN_IDS else "admin")
        from services.audit_service import log_audit_event
        log_audit_event(
            workspace_id=str(workspace_id),
            actor_user_id=int(unbanned_by),
            actor_role=b_role,
            action="member_unbanned",
            resource=f"user:{uid}",
            details={"unbanned_user_id": uid}
        )
    return True
