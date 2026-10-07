from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal
import uuid
from datetime import datetime
from database.models import Transaction
from database.db import get_db_connection, LEDGER_LOCK
from utils.dates import build_occurred_at, utc_now_iso
from utils.validation import (
    parse_decimal_amount,
    validate_transaction_type,
    validate_uid,
    validate_string_length,
    CENT,
)
from config import logger

def increment_revision_and_mark_dirty(conn) -> int:
    """
    Increments the persisted backup_revision counter in settings by 1 and marks is_dirty = '1'.
    Must be called inside the active database transaction of a committed mutation under LEDGER_LOCK.
    Returns the new revision integer.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
    row = cursor.fetchone()
    current_rev = int(row['value']) if row and str(row['value']).isdigit() else 1
    new_rev = current_rev + 1
    now_utc = utc_now_iso()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_revision', ?, ?)",
        (str(new_rev), now_utc)
    )
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '1', ?)",
        (now_utc,)
    )
    return new_rev


# ==============================================================================
# WORKSPACE & MULTI-TENANT MANAGEMENT
# ==============================================================================

def get_default_workspace_id() -> str:
    """
    Returns the system's default workspace ID from settings.
    Provisions one if missing (idempotent fallback).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
        row = cursor.fetchone()
        if row and row['value']:
            return str(row['value'])
        
        # Fallback: check workspaces table for any existing workspace
        cursor.execute("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1")
        ws_row = cursor.fetchone()
        if ws_row and ws_row['id']:
            ws_id = str(ws_row['id'])
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('default_workspace_id', ?, ?)", (ws_id, utc_now_iso()))
            conn.commit()
            return ws_id

        # Provision new default workspace
        new_ws_id = str(uuid.uuid4())
        now_utc = utc_now_iso()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('default_workspace_id', ?, ?)", (new_ws_id, now_utc))
        cursor.execute("""
            INSERT OR IGNORE INTO workspaces (id, chat_id, chat_type, title, is_active, created_at, updated_at)
            VALUES (?, 0, 'private', 'Primary Workspace', 1, ?, ?)
        """, (new_ws_id, now_utc, now_utc))
        conn.commit()
        return new_ws_id

class RowDict(dict):
    """Dict subclass allowing dot-notation attribute access alongside standard dict indexing."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'RowDict' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        try:
            del self[name]
        except KeyError:
            raise AttributeError(f"'RowDict' object has no attribute '{name}'")

def get_or_create_workspace(
    chat_id: int | str,
    chat_type: str = "private",
    title: str = "",
    creator_user_id: Optional[int] = None,
    username: str = "",
    display_name: str = ""
) -> RowDict:
    """
    Retrieves an existing workspace by chat_id or creates a new one.
    Thread-safe under LEDGER_LOCK.
    """
    norm_chat_type = str(chat_type or "private").lower()
    if norm_chat_type == "private":
        norm_chat_type = "dm"

    with LEDGER_LOCK:
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM workspaces WHERE chat_id = ? AND is_active = 1", (int(chat_id),))
            row = cursor.fetchone()
            if row:
                ws_data = RowDict(dict(row))
                if creator_user_id is not None:
                    add_workspace_member(
                        workspace_id=ws_data['id'],
                        telegram_user_id=int(creator_user_id),
                        username=username,
                        display_name=display_name,
                        role="owner"
                    )
                return ws_data
            
            ws_id = str(uuid.uuid4())
            clean_title = (title or "").strip()[:200]
            cursor.execute("""
                INSERT INTO workspaces (id, chat_id, chat_type, title, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
            """, (ws_id, int(chat_id), norm_chat_type, clean_title, now_utc, now_utc))
            conn.commit()

            if creator_user_id is not None:
                add_workspace_member(
                    workspace_id=ws_id,
                    telegram_user_id=int(creator_user_id),
                    username=username,
                    display_name=display_name,
                    role="owner"
                )

            return RowDict({
                'id': ws_id,
                'chat_id': int(chat_id),
                'chat_type': norm_chat_type,
                'title': clean_title,
                'is_active': 1,
                'created_at': now_utc,
                'updated_at': now_utc
            })

def get_workspace_by_id(workspace_id: str) -> RowDict | None:
    """Fetches a workspace by its unique ID."""
    if not workspace_id:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM workspaces WHERE id = ?", (str(workspace_id),))
        row = cursor.fetchone()
        return RowDict(dict(row)) if row else None

def get_workspace_by_chat_id(chat_id: int | str) -> RowDict | None:
    """Fetches a workspace by Telegram chat ID."""
    if chat_id is None:
        return None
    try:
        c_int = int(chat_id)
    except (ValueError, TypeError):
        c_int = None
    c_str = str(chat_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if c_int is not None:
            cursor.execute("SELECT * FROM workspaces WHERE (chat_id = ? OR chat_id = ?) AND is_active = 1", (c_int, c_str))
        else:
            cursor.execute("SELECT * FROM workspaces WHERE chat_id = ? AND is_active = 1", (c_str,))
        row = cursor.fetchone()
        return RowDict(dict(row)) if row else None

def get_all_active_workspaces() -> list[RowDict]:
    """Fetches all active workspaces."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM workspaces WHERE is_active = 1 ORDER BY created_at ASC")
        return [RowDict(dict(r)) for r in cursor.fetchall()]

def update_workspace_title(workspace_id: str, title: str) -> None:
    """Updates the title of a workspace."""
    if not workspace_id or not title:
        return
    with LEDGER_LOCK:
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE workspaces SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip()[:200], now_utc, str(workspace_id))
            )
            conn.commit()

def create_custom_workspace(
    title: str,
    creator_user_id: int | str,
    username: str = "",
    display_name: str = ""
) -> RowDict:
    """Creates a custom standalone workspace not bound to a telegram chat."""
    import time
    synthetic_chat_id = -abs(int(time.time() * 1000) % 2000000000 + 1000000000)
    return get_or_create_workspace(
        chat_id=synthetic_chat_id,
        chat_type="group",
        title=title,
        creator_user_id=creator_user_id,
        username=username,
        display_name=display_name
    )

def ensure_all_user_workspaces(current_chat_title: Optional[str] = None, current_chat_id: Optional[int] = None) -> None:
    """
    Ensures that:
    1. The owner and all known users/members have their personal DM workspaces provisioned.
    2. Group chat workspace title reflects the real group title (e.g. Payment (Group) instead of Primary Workspace).
    3. Global bot owner is an admin/owner of all workspaces so they can inspect and manage them.
    """
    import config
    owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
    try:
        owner_id = int(owner_id) if owner_id is not None else None
    except (ValueError, TypeError):
        owner_id = None

    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 1. Update group chat titles and ensure members for all group workspaces
            now_utc = utc_now_iso()
            cursor.execute("SELECT id, chat_id, title FROM workspaces WHERE (chat_type IN ('group', 'supergroup') OR chat_id < 0) AND is_active = 1")
            group_workspaces_list = cursor.fetchall()
            if not group_workspaces_list and current_chat_id is not None and int(current_chat_id) < 0:
                cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
                d_row = cursor.fetchone()
                if d_row and d_row['value']:
                    new_grp_id = str(d_row['value'])
                else:
                    cursor.execute("""
                        SELECT workspace_id, COUNT(*) as cnt 
                        FROM transactions 
                        WHERE workspace_id IS NOT NULL AND workspace_id != '' 
                        GROUP BY workspace_id 
                        ORDER BY cnt DESC LIMIT 1
                    """)
                    top_tx = cursor.fetchone()
                    if top_tx and top_tx['workspace_id']:
                        new_grp_id = str(top_tx['workspace_id'])
                    else:
                        new_grp_id = "d2b59f0c-e09a-40cf-9497-819cecfe4173"

                c_title = (current_chat_title or "Payment").strip()
                if not c_title.endswith("(Group)"):
                    c_title = f"{c_title} (Group)"
                cursor.execute("""
                    INSERT INTO workspaces (id, chat_id, chat_type, title, is_active, created_at, updated_at)
                    VALUES (?, ?, 'supergroup', ?, 1, ?, ?)
                """, (new_grp_id, int(current_chat_id), c_title, now_utc, now_utc))
                group_workspaces_list = [{'id': new_grp_id, 'chat_id': int(current_chat_id), 'title': c_title}]

            # Determine canonical default group workspace
            canonical_default_ws_id = None
            if current_chat_id is not None and int(current_chat_id) < 0:
                for g_row in group_workspaces_list:
                    if g_row['chat_id'] == int(current_chat_id):
                        canonical_default_ws_id = g_row['id']
                        break

            if not canonical_default_ws_id:
                import config
                tg_grp = getattr(config, 'TELEGRAM_GROUP_ID', None)
                if tg_grp:
                    for g_row in group_workspaces_list:
                        if g_row['chat_id'] == int(tg_grp):
                            canonical_default_ws_id = g_row['id']
                            break

            if not canonical_default_ws_id:
                for g_row in group_workspaces_list:
                    if g_row.get('title') and 'Payment' in g_row['title']:
                        canonical_default_ws_id = g_row['id']
                        break

            if not canonical_default_ws_id and group_workspaces_list:
                canonical_default_ws_id = group_workspaces_list[0]['id']

            if not canonical_default_ws_id:
                canonical_default_ws_id = "d2b59f0c-e09a-40cf-9497-819cecfe4173"

            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('default_workspace_id', ?, ?)", (canonical_default_ws_id, now_utc))

            # Adopt all legacy, orphaned, or unassigned transactions into the canonical default group workspace:
            cursor.execute("""
                UPDATE transactions 
                SET workspace_id = ? 
                WHERE workspace_id IS NULL 
                   OR workspace_id = '' 
                   OR workspace_id = '39648d95-f24d-4459-be59-40c62e13df85'
                   OR workspace_id NOT IN (SELECT id FROM workspaces)
            """, (canonical_default_ws_id,))

            for g_row in group_workspaces_list:
                grp_id = g_row['id']
                grp_chat_id = g_row['chat_id']

                if current_chat_id is not None and current_chat_title and grp_chat_id == int(current_chat_id):
                    clean_title = current_chat_title.strip()
                    if not clean_title.endswith("(Group)"):
                        clean_title = f"{clean_title} (Group)"
                    if not g_row['title'] or g_row['title'] in ('Primary Workspace', 'Workspace', '') or g_row['title'].startswith('Chat_'):
                        cursor.execute(
                            "UPDATE workspaces SET title = ?, updated_at = ? WHERE id = ?",
                            (clean_title, now_utc, grp_id)
                        )
                elif not g_row['title'] or g_row['title'] in ('Primary Workspace', 'Workspace', ''):
                    cursor.execute(
                        "UPDATE workspaces SET title = 'Payment (Group)', updated_at = ? WHERE id = ?",
                        (now_utc, grp_id)
                    )

                if owner_id:
                    cursor.execute("SELECT id FROM workspace_members WHERE workspace_id = ? AND telegram_user_id = ?", (grp_id, owner_id))
                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT OR REPLACE INTO workspace_members 
                            (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                            VALUES (?, ?, 'pranav', 'Pranav', 'owner', 1, ?, ?)
                        """, (grp_id, owner_id, now_utc, now_utc))
                    else:
                        cursor.execute("UPDATE workspace_members SET role = 'owner', is_active = 1 WHERE workspace_id = ? AND telegram_user_id = ?", (grp_id, owner_id))

                # Ensure Nagendra is in the group workspace
                cursor.execute("SELECT id, role FROM workspace_members WHERE workspace_id = ? AND telegram_user_id = ?", (grp_id, 8343764796))
                nag_row = cursor.fetchone()
                if not nag_row:
                    cursor.execute("""
                        INSERT OR REPLACE INTO workspace_members 
                        (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                        VALUES (?, ?, 'nagendra', 'Nagendra', 'member', 1, ?, ?)
                    """, (grp_id, 8343764796, now_utc, now_utc))
                else:
                    if nag_row['role'] == 'owner':
                        cursor.execute("UPDATE workspace_members SET role = 'member', is_active = 1 WHERE workspace_id = ? AND telegram_user_id = 8343764796", (grp_id,))

            # 2. Collect all known user IDs with their best display names / usernames
            known_users: dict[int, tuple[str, str]] = {}
            if owner_id:
                known_users[owner_id] = ("Pranav", "pranav")
            known_users[8343764796] = ("Nagendra", "nagendra")

            # From workspace_members
            cursor.execute("SELECT telegram_user_id, display_name, username FROM workspace_members")
            for r in cursor.fetchall():
                try:
                    uid = int(r['telegram_user_id'])
                    dname = r['display_name'] or ""
                    uname = r['username'] or ""
                    if uid not in known_users or (not known_users[uid][0] and dname):
                        known_users[uid] = (dname or f"User {uid}", uname)
                except Exception:
                    pass

            # From access_requests
            cursor.execute("SELECT telegram_user_id, display_name, username FROM access_requests")
            for r in cursor.fetchall():
                try:
                    uid = int(r['telegram_user_id'])
                    dname = r['display_name'] or ""
                    uname = r['username'] or ""
                    if uid not in known_users or (not known_users[uid][0] and dname):
                        known_users[uid] = (dname or f"User {uid}", uname)
                except Exception:
                    pass

            # From transactions
            cursor.execute("SELECT DISTINCT telegram_user_id, person_name FROM transactions WHERE telegram_user_id IS NOT NULL")
            for r in cursor.fetchall():
                try:
                    uid = int(r['telegram_user_id'])
                    pname = r['person_name'] or ""
                    if uid not in known_users:
                        known_users[uid] = (pname or f"User {uid}", "")
                except Exception:
                    pass

            # 3. For every known user, ensure a DM personal workspace exists (chat_id = uid, chat_type = 'dm')
            # NOTE: Owner (Pranav) explicitly uses Payment (Group) only - skip personal workspace creation for owner.
            for uid, (dname, uname) in known_users.items():
                if uid <= 0 or (owner_id and uid == owner_id):
                    continue  # groups have negative chat_ids; owner only uses Payment (Group)

                cursor.execute("SELECT id, title FROM workspaces WHERE chat_id = ? AND is_active = 1", (uid,))
                ws_row = cursor.fetchone()
                clean_name = "Nagendra" if uid == 8343764796 else (dname if dname and not dname.startswith("User ") else (uname or f"User {uid}"))
                ws_title = f"{clean_name} (Personal)"
                user_role = 'member'

                if not ws_row:
                    ws_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO workspaces (id, chat_id, chat_type, title, is_active, created_at, updated_at)
                        VALUES (?, ?, 'dm', ?, 1, ?, ?)
                    """, (ws_id, uid, ws_title, now_utc, now_utc))
                    cursor.execute("""
                        INSERT OR REPLACE INTO workspace_members 
                        (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """, (ws_id, uid, uname, clean_name, user_role, now_utc, now_utc))
                    if owner_id and owner_id != uid:
                        cursor.execute("""
                            INSERT OR REPLACE INTO workspace_members 
                            (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                            VALUES (?, ?, 'owner', 'Owner', 'owner', 1, ?, ?)
                        """, (ws_id, owner_id, now_utc, now_utc))
                else:
                    curr_title = ws_row['title'] or ""
                    if curr_title in ("Workspace", "Primary Workspace", "") or curr_title == str(uid):
                        cursor.execute(
                            "UPDATE workspaces SET title = ?, updated_at = ? WHERE id = ?",
                            (ws_title, now_utc, ws_row['id'])
                        )
                    if owner_id and owner_id != uid:
                        cursor.execute("SELECT id FROM workspace_members WHERE workspace_id = ? AND telegram_user_id = ?", (ws_row['id'], owner_id))
                        if not cursor.fetchone():
                            cursor.execute("""
                                INSERT OR REPLACE INTO workspace_members 
                                (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                                VALUES (?, ?, 'owner', 'Owner', 'owner', 1, ?, ?)
                            """, (ws_row['id'], owner_id, now_utc, now_utc))

            # 4. Clean up any personal workspace for owner (Pranav) - user requested Payment (Group) only
            cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
            d_row = cursor.fetchone()
            default_ws_id = str(d_row['value']) if d_row and d_row['value'] else get_default_workspace_id()

            if owner_id:
                cursor.execute("SELECT id FROM workspaces WHERE chat_id = ?", (owner_id,))
                personal_rows = cursor.fetchall()
                for p_row in personal_rows:
                    p_id = p_row['id']
                    cursor.execute("UPDATE transactions SET workspace_id = ? WHERE workspace_id = ?", (default_ws_id, p_id))
                    cursor.execute("DELETE FROM workspace_members WHERE workspace_id = ?", (p_id,))
                    cursor.execute("DELETE FROM workspaces WHERE id = ?", (p_id,))
                cursor.execute("DELETE FROM workspace_settings WHERE key = ?", (f"user_active_ws:{owner_id}",))

            # 5. Enforce Nagendra (8343764796) is never owner across all workspaces
            cursor.execute("UPDATE workspace_members SET role = 'member' WHERE telegram_user_id = 8343764796 AND role = 'owner'")

            # 6. Recalculate balance for default workspace
            from services.balance_service import recalculate_in_connection
            try:
                recalculate_in_connection(conn, workspace_id=default_ws_id)
            except Exception as b_err:
                logger.warning(f"ensure_all_user_workspaces balance recalculation notice: {b_err}")

            conn.commit()

def get_user_workspaces(telegram_user_id: int | str) -> list[RowDict]:
    """Fetches all workspaces where the given user is an active member."""
    if not telegram_user_id:
        return []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT w.*, wm.role as user_role 
            FROM workspaces w
            JOIN workspace_members wm ON w.id = wm.workspace_id
            WHERE wm.telegram_user_id = ? AND wm.is_active = 1 AND w.is_active = 1
            ORDER BY w.created_at ASC
        """, (int(telegram_user_id),))
        return [RowDict(dict(r)) for r in cursor.fetchall()]

def get_workspace_member(workspace_id: str, telegram_user_id: int | str) -> RowDict | None:
    """Fetches membership record for a specific user in a workspace."""
    if not workspace_id or not telegram_user_id:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM workspace_members 
            WHERE workspace_id = ? AND telegram_user_id = ? AND is_active = 1
        """, (str(workspace_id), int(telegram_user_id)))
        row = cursor.fetchone()
        return RowDict(dict(row)) if row else None

def get_all_workspace_members(workspace_id: str) -> list[RowDict]:
    """Fetches all active members in a workspace."""
    if not workspace_id:
        return []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM workspace_members 
            WHERE workspace_id = ? AND is_active = 1 
            ORDER BY joined_at ASC
        """, (str(workspace_id),))
        return [RowDict(dict(r)) for r in cursor.fetchall()]

def add_workspace_member(
    workspace_id: str,
    telegram_user_id: int | str,
    username: str = "",
    display_name: str = "",
    role: str = "member"
) -> RowDict:
    """
    Adds or updates a user's membership in a workspace.
    Valid roles: 'owner', 'admin', 'member', 'viewer'.
    """
    valid_roles = {'owner', 'admin', 'member', 'viewer'}
    clean_role = role.lower() if role and role.lower() in valid_roles else 'member'
    if int(telegram_user_id) == 8343764796 and clean_role == 'owner':
        clean_role = 'member'
    now_utc = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO workspace_members (
                    workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(workspace_id, telegram_user_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    role = excluded.role,
                    is_active = 1,
                    updated_at = excluded.updated_at
            """, (str(workspace_id), int(telegram_user_id), str(username or ""), str(display_name or ""), clean_role, now_utc, now_utc))
            conn.commit()
            return RowDict({
                'workspace_id': str(workspace_id),
                'telegram_user_id': int(telegram_user_id),
                'username': str(username or ""),
                'display_name': str(display_name or ""),
                'role': clean_role,
                'is_active': 1,
                'updated_at': now_utc
            })

def update_workspace_member_role(workspace_id: str, telegram_user_id: int, new_role: str) -> bool:
    """Updates role for a workspace member."""
    valid_roles = {'owner', 'admin', 'member', 'viewer'}
    if not new_role or new_role.lower() not in valid_roles:
        raise ValueError(f"Invalid role: {new_role}. Allowed: {sorted(valid_roles)}")
    if int(telegram_user_id) == 8343764796 and new_role.lower() == 'owner':
        raise ValueError("Nagendra cannot be assigned the owner role.")
    now_utc = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE workspace_members 
                SET role = ?, updated_at = ? 
                WHERE workspace_id = ? AND telegram_user_id = ? AND is_active = 1
            """, (new_role.lower(), now_utc, str(workspace_id), int(telegram_user_id)))
            conn.commit()
            return cursor.rowcount > 0

def remove_workspace_member(workspace_id: str, telegram_user_id: int | str) -> bool:
    """Removes a user from a workspace and clears any active workspace override."""
    if not workspace_id or not telegram_user_id:
        return False
    uid = int(telegram_user_id)
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM workspace_settings WHERE key = ?", (f"user_active_ws:{uid}",))
            cursor.execute(
                "DELETE FROM workspace_members WHERE workspace_id = ? AND telegram_user_id = ?",
                (str(workspace_id), uid)
            )
            conn.commit()
            removed = cursor.rowcount > 0
    try:
        from bot.auth import set_user_active_workspace
        set_user_active_workspace(uid, None)
    except Exception:
        pass
    return removed

def get_access_request(telegram_user_id: int) -> RowDict | None:
    """Fetches access request for a user."""
    if not telegram_user_id:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM access_requests WHERE telegram_user_id = ?", (int(telegram_user_id),))
        row = cursor.fetchone()
        return RowDict(dict(row)) if row else None

def create_access_request(telegram_user_id: int, username: str, display_name: str, chat_id: int, chat_type: str = "private", workspace_id: str = None) -> RowDict:
    """Creates or updates a pending access request."""
    now_utc = utc_now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO access_requests (telegram_user_id, workspace_id, username, display_name, chat_id, chat_type, status, requested_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                workspace_id = COALESCE(excluded.workspace_id, access_requests.workspace_id),
                username = excluded.username,
                display_name = excluded.display_name,
                chat_id = excluded.chat_id,
                chat_type = excluded.chat_type,
                status = 'pending',
                requested_at = excluded.requested_at
        """, (int(telegram_user_id), str(workspace_id) if workspace_id else None, str(username or ""), str(display_name or ""), int(chat_id), str(chat_type or "private"), now_utc))
        conn.commit()
        return RowDict({
            'telegram_user_id': int(telegram_user_id),
            'workspace_id': str(workspace_id) if workspace_id else None,
            'username': str(username or ""),
            'display_name': str(display_name or ""),
            'chat_id': int(chat_id),
            'chat_type': str(chat_type or "private"),
            'status': 'pending',
            'requested_at': now_utc
        })


def update_access_request_status(telegram_user_id: int, status: str, reviewed_by: int = None) -> bool:
    """Updates access request status ('approved', 'rejected', 'pending')."""
    now_utc = utc_now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE access_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?
            WHERE telegram_user_id = ?
        """, (str(status).lower(), now_utc, int(reviewed_by) if reviewed_by else None, int(telegram_user_id)))
        conn.commit()
        return cursor.rowcount > 0

def get_all_users_for_permissions() -> list[RowDict]:
    """Fetches all known users from workspace_members and access_requests for permission administration."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                u.telegram_user_id,
                MAX(COALESCE(u.username, '')) as username,
                MAX(COALESCE(u.display_name, '')) as display_name,
                MAX(COALESCE(u.is_active, 0)) as is_active,
                MAX(u.joined_at) as joined_at,
                MAX(COALESCE(u.request_status, 'approved')) as request_status,
                CASE MAX(
                    CASE u.role
                        WHEN 'owner' THEN 4
                        WHEN 'admin' THEN 3
                        WHEN 'member' THEN 2
                        WHEN 'viewer' THEN 1
                        ELSE 0
                    END
                )
                    WHEN 4 THEN 'owner'
                    WHEN 3 THEN 'admin'
                    WHEN 2 THEN 'member'
                    WHEN 1 THEN 'viewer'
                    ELSE 'viewer'
                END as role
            FROM (
                SELECT 
                    wm.telegram_user_id,
                    wm.username,
                    wm.display_name,
                    wm.role,
                    wm.is_active,
                    wm.joined_at,
                    COALESCE(ar.status, 'approved') as request_status
                FROM workspace_members wm
                LEFT JOIN access_requests ar ON wm.telegram_user_id = ar.telegram_user_id
                UNION ALL
                SELECT 
                    ar.telegram_user_id,
                    ar.username,
                    ar.display_name,
                    'viewer' as role,
                    CASE WHEN ar.status = 'approved' THEN 1 ELSE 0 END as is_active,
                    ar.requested_at as joined_at,
                    ar.status as request_status
                FROM access_requests ar
                WHERE ar.telegram_user_id NOT IN (SELECT telegram_user_id FROM workspace_members)
            ) u
            GROUP BY u.telegram_user_id
            ORDER BY is_active DESC, 
                CASE role
                    WHEN 'owner' THEN 4
                    WHEN 'admin' THEN 3
                    WHEN 'member' THEN 2
                    WHEN 'viewer' THEN 1
                    ELSE 0
                END DESC, 
                telegram_user_id ASC
        """)
        return [RowDict(dict(r)) for r in cursor.fetchall()]

def set_user_permission_and_role(telegram_user_id: int, role: str, is_active: bool = True) -> bool:
    """Updates role and active status for a user across all their workspaces."""
    valid_roles = {'owner', 'admin', 'member', 'viewer'}
    clean_role = role.lower() if role and role.lower() in valid_roles else 'member'
    if int(telegram_user_id) == 8343764796 and clean_role == 'owner':
        clean_role = 'member'
    active_val = 1 if is_active else 0
    now_utc = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE workspace_members
                SET role = ?, is_active = ?, updated_at = ?
                WHERE telegram_user_id = ?
            """, (clean_role, active_val, now_utc, int(telegram_user_id)))

            if is_active:
                def_ws_id = get_default_workspace_id()
                if def_ws_id:
                    cursor.execute(
                        "SELECT id FROM workspace_members WHERE workspace_id = ? AND telegram_user_id = ?",
                        (def_ws_id, int(telegram_user_id))
                    )
                    if not cursor.fetchone():
                        cursor.execute("SELECT username, display_name FROM access_requests WHERE telegram_user_id = ?", (int(telegram_user_id),))
                        ar_row = cursor.fetchone()
                        uname = ar_row['username'] if ar_row and ar_row['username'] else ""
                        dname = ar_row['display_name'] if ar_row and ar_row['display_name'] else ""
                        cursor.execute("""
                            INSERT OR REPLACE INTO workspace_members 
                            (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (def_ws_id, int(telegram_user_id), uname, dname, clean_role, active_val, now_utc, now_utc))

            req_status = 'approved' if is_active else 'rejected'
            cursor.execute("""
                UPDATE access_requests
                SET status = ?, reviewed_at = ?
                WHERE telegram_user_id = ?
            """, (req_status, now_utc, int(telegram_user_id)))
            conn.commit()
            return True

def get_workspace_setting(workspace_id: str, key: str, default: str = None) -> str | None:
    """
    Dual-read helper: checks workspace_settings first, then global settings table.
    """
    if not key:
        return default
    ws_id = workspace_id or get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM workspace_settings WHERE workspace_id = ? AND key = ?", (ws_id, key))
        row = cursor.fetchone()
        if row and row['value'] is not None:
            return str(row['value'])
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        global_row = cursor.fetchone()
        if global_row and global_row['value'] is not None:
            return str(global_row['value'])
        return default

def set_workspace_setting(workspace_id: str, key: str, value: str) -> str:
    """
    Sets a setting in workspace_settings.
    If workspace is the default workspace, mirrors to global settings table for 100% backward compatibility.
    """
    ws_id = workspace_id or get_default_workspace_id()
    now_utc = utc_now_iso()
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
            """, (ws_id, key, str(value), now_utc))
            
            # Mirror to global settings if this is the default workspace
            default_ws = get_default_workspace_id()
            if ws_id == default_ws:
                cursor.execute("""
                    INSERT OR REPLACE INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, str(value), now_utc))
            conn.commit()
            return str(value)


# ==============================================================================
# TRANSACTION MUTATIONS & QUERIES
# ==============================================================================

def insert_transaction_with_balance(t: Transaction) -> int:
    """
    Inserts a transaction and recalculates the balance chain atomically.
    Executes in ONE connection and ONE database transaction under LEDGER_LOCK:
      1. Validates all inputs (amount, type, UID, string lengths).
      2. Checks duplicate reference_number among live transactions in the workspace (deleted_at IS NULL).
      3. Computes occurred_at, created_at, updated_at using utc_now_iso().
      4. Inserts the transaction row with placeholder balances.
      5. Recalculates the entire ledger chain using recalculate_in_connection(conn, workspace_id).
      6. Commits the transaction (or rolls back on ANY failure, leaving balance unchanged).
    A back-dated receipt produces a correct chain.
    Does NOT export backup here.
    """
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection

        # Step 1: Validate inputs
        dec_amount = parse_decimal_amount(t.amount, allow_zero=False)
        t.amount = float(dec_amount)
        t.transaction_type = validate_transaction_type(t.transaction_type)
        category = validate_string_length(getattr(t, 'category', 'General') or 'General', max_length=100, field_name="Category")
        t.category = category

        raw_uid = getattr(t, 'uid', None)
        if raw_uid:
            tx_uid = validate_uid(raw_uid)
        else:
            tx_uid = uuid.uuid4().hex
        t.uid = tx_uid

        # Resolve workspace_id
        ws_id = getattr(t, 'workspace_id', None) or get_default_workspace_id()
        t.workspace_id = ws_id

        t.person_name = validate_string_length(t.person_name, max_length=120, field_name="Person name")
        t.sender_name = validate_string_length(t.sender_name, max_length=120, field_name="Sender name")
        t.recipient_name = validate_string_length(t.recipient_name, max_length=120, field_name="Recipient name")
        t.reference_number = validate_string_length(t.reference_number, max_length=100, field_name="Reference number")

        # Step 2: Compute occurred_at and timestamps
        occurred_at = build_occurred_at(t.transaction_date, t.transaction_time)
        t.occurred_at = occurred_at

        now_utc = utc_now_iso()
        created_at = getattr(t, 'created_at', None) or now_utc
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        updated_at = now_utc

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Step 3: Check duplicate reference among live rows in THIS workspace
            if t.reference_number:
                cursor.execute(
                    "SELECT id FROM transactions WHERE reference_number = ? AND workspace_id = ? AND deleted_at IS NULL",
                    (t.reference_number, ws_id)
                )
                dup = cursor.fetchone()
                if dup:
                    raise ValueError(f"Duplicate live reference number: {t.reference_number}")

            # Step 4: Insert row
            cursor.execute("PRAGMA table_info(transactions)")
            t_cols = [r[1] for r in cursor.fetchall()]
            has_uid_col = "telegram_user_id" in t_cols
            t_user_id = getattr(t, 'telegram_user_id', None)

            date_val = str(t.transaction_date) if t.transaction_date is not None else None
            time_val = str(t.transaction_time) if t.transaction_time is not None else None

            if has_uid_col:
                query = '''
                    INSERT INTO transactions (
                        transaction_type, amount, person_name, sender_name, recipient_name,
                        upi_id, phone_number, transaction_date, transaction_time, reference_number,
                        transaction_id, payment_app, bank_name, bank_account, payment_status,
                        category, balance_before, balance_after, ocr_text, original_image_path,
                        telegram_message_id, telegram_chat_id, telegram_user_id, uid, workspace_id, deleted_at,
                        occurred_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                '''
                values = (
                    t.transaction_type, t.amount, t.person_name, t.sender_name, t.recipient_name,
                    t.upi_id, t.phone_number, date_val, time_val, t.reference_number,
                    t.transaction_id, t.payment_app, t.bank_name, t.bank_account, t.payment_status,
                    category, 0.0, 0.0, t.ocr_text, t.original_image_path,
                    t.telegram_message_id, t.telegram_chat_id, t_user_id, tx_uid, ws_id, getattr(t, 'deleted_at', None),
                    occurred_at, created_at, updated_at
                )
            else:
                query = '''
                    INSERT INTO transactions (
                        transaction_type, amount, person_name, sender_name, recipient_name,
                        upi_id, phone_number, transaction_date, transaction_time, reference_number,
                        transaction_id, payment_app, bank_name, bank_account, payment_status,
                        category, balance_before, balance_after, ocr_text, original_image_path,
                        telegram_message_id, telegram_chat_id, uid, workspace_id, deleted_at,
                        occurred_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                '''
                values = (
                    t.transaction_type, t.amount, t.person_name, t.sender_name, t.recipient_name,
                    t.upi_id, t.phone_number, date_val, time_val, t.reference_number,
                    t.transaction_id, t.payment_app, t.bank_name, t.bank_account, t.payment_status,
                    category, 0.0, 0.0, t.ocr_text, t.original_image_path,
                    t.telegram_message_id, t.telegram_chat_id, tx_uid, ws_id, getattr(t, 'deleted_at', None),
                    occurred_at, created_at, updated_at
                )

            cursor.execute(query, values)
            new_id = cursor.lastrowid
            t.id = new_id

            # Step 5: Recalculate chain in this connection for the workspace
            recalculate_in_connection(conn, workspace_id=ws_id)
            increment_revision_and_mark_dirty(conn)
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('database_initialized', '1', ?)", (now_utc,))
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('backup_blocked', '0', ?)", (now_utc,))
            cursor.execute("INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, 'database_initialized', '1', ?)", (ws_id, now_utc))
            cursor.execute("INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, 'backup_blocked', '0', ?)", (ws_id, now_utc))

            # Step 6: Commit happens on exit of context manager
            return new_id

def insert_transaction(t: Transaction) -> int:
    """Inserts a new transaction into the database with decimal validation, occurred_at, and thread locking."""
    with LEDGER_LOCK:
        return insert_transaction_with_balance(t)

def get_transaction_author_id(tx_id: int | str) -> Optional[int]:
    """Retrieves the telegram_user_id who created this transaction."""
    if not tx_id:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transactions)")
        cols = [r[1] for r in cursor.fetchall()]
        if "telegram_user_id" in cols:
            cursor.execute("SELECT telegram_user_id, uid FROM transactions WHERE id = ?", (tx_id,))
            row = cursor.fetchone()
            if row:
                if row['telegram_user_id']:
                    return int(row['telegram_user_id'])
                t_uid = row['uid']
                if t_uid:
                    cursor.execute("SELECT user_id FROM undo_log WHERE uid = ? ORDER BY id ASC LIMIT 1", (t_uid,))
                    u_row = cursor.fetchone()
                    if u_row and u_row['user_id']:
                        return int(u_row['user_id'])
        else:
            cursor.execute("SELECT uid FROM transactions WHERE id = ?", (tx_id,))
            row = cursor.fetchone()
            if row and row['uid']:
                cursor.execute("SELECT user_id FROM undo_log WHERE uid = ? ORDER BY id ASC LIMIT 1", (row['uid'],))
                u_row = cursor.fetchone()
                if u_row and u_row['user_id']:
                    return int(u_row['user_id'])
    return None

def can_user_modify_transaction(tx_id: int | str, user_id: int, user_role: str) -> bool:
    """Evaluates whether the caller can edit or delete this transaction."""
    if user_role in ('owner', 'admin'):
        return True
    if user_role == 'member':
        author_id = get_transaction_author_id(tx_id)
        if author_id is not None and int(author_id) == int(user_id):
            return True
    return False

def get_user_recent_transactions(user_id: int, workspace_id: str = None, limit: int = 6) -> list:
    """Fetches recent active transactions created by a specific user."""
    ws_id = workspace_id or get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transactions)")
        cols = [r[1] for r in cursor.fetchall()]
        has_uid = "telegram_user_id" in cols

        if has_uid:
            cursor.execute("""
                SELECT DISTINCT t.* FROM transactions t
                LEFT JOIN undo_log u ON u.transaction_id = t.id AND u.action_type = 'INSERT'
                WHERE (t.workspace_id = ? OR t.workspace_id IS NULL)
                  AND t.deleted_at IS NULL
                  AND (t.telegram_user_id = ? OR u.user_id = ?)
                ORDER BY t.occurred_at DESC, t.id DESC
                LIMIT ?
            """, (ws_id, int(user_id), int(user_id), limit))
        else:
            cursor.execute("""
                SELECT DISTINCT t.* FROM transactions t
                JOIN undo_log u ON u.transaction_id = t.id AND u.action_type = 'INSERT'
                WHERE (t.workspace_id = ? OR t.workspace_id IS NULL)
                  AND t.deleted_at IS NULL
                  AND u.user_id = ?
                ORDER BY t.occurred_at DESC, t.id DESC
                LIMIT ?
            """, (ws_id, int(user_id), limit))
        return [dict(r) for r in cursor.fetchall()]

def get_transaction_by_reference(reference_number: str, workspace_id: str = None):
    """Fetches a transaction by its reference number with workspace isolation and dual-read fallback."""
    if not reference_number:
        return None
    ws_id = workspace_id or get_default_workspace_id()
    default_ws = get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions WHERE reference_number = ? AND (workspace_id = ? OR workspace_id = ? OR workspace_id IS NULL) AND deleted_at IS NULL",
            (reference_number, ws_id, default_ws)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_recent_transactions(limit: int = 10, workspace_id: str = None):
    """Fetches recent transactions ordered by occurred_at, created_at, and id with workspace isolation."""
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM transactions WHERE {ws_filter} AND deleted_at IS NULL ORDER BY occurred_at DESC, created_at DESC, id DESC LIMIT ?",
            (ws_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

def get_transactions_by_date(target_date, workspace_id: str = None):
    """Fetches transactions for a specific date with workspace isolation."""
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM transactions WHERE {ws_filter} AND transaction_date = ? AND deleted_at IS NULL ORDER BY occurred_at ASC, created_at ASC, id ASC",
            (ws_id, str(target_date))
        )
        return [dict(row) for row in cursor.fetchall()]

def get_all_transactions(workspace_id: str = None):
    """Fetches all transactions for export (newest first) with workspace isolation."""
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM transactions WHERE {ws_filter} AND deleted_at IS NULL ORDER BY occurred_at DESC, created_at DESC, id DESC",
            (ws_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def get_all_transactions_asc(workspace_id: str = None):
    """Fetches all transactions in ascending order (oldest first, ID #1 first) with workspace isolation."""
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM transactions WHERE {ws_filter} AND deleted_at IS NULL ORDER BY occurred_at ASC, created_at ASC, id ASC",
            (ws_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def search_transactions(
    query_text: str = "",
    target_date = None,
    month: int = None,
    year: int = None,
    tx_type: str = "",
    person: str = "",
    exact_amount: float = None,
    sort_by: str = "date_desc",
    limit: int = 50,
    workspace_id: str = None
):
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    conditions = [ws_filter, "deleted_at IS NULL"]
    params = [ws_id]
    
    if exact_amount is not None and float(exact_amount) > 0:
        conditions.append("amount = ?")
        params.append(float(exact_amount))

    if target_date:
        conditions.append("transaction_date = ?")
        params.append(str(target_date))
    elif month and year:
        # SQLite strftime for month and year
        month_str = f"{year:04d}-{month:02d}"
        conditions.append("strftime('%Y-%m', transaction_date) = ?")
        params.append(month_str)
    elif year:
        conditions.append("strftime('%Y', transaction_date) = ?")
        params.append(str(year))
        
    if tx_type:
        conditions.append("transaction_type = ?")
        params.append(tx_type.upper())
        
    if person:
        conditions.append("(person_name LIKE ? OR sender_name LIKE ? OR recipient_name LIKE ?)")
        p_pattern = f"%{person}%"
        params.extend([p_pattern, p_pattern, p_pattern])
        
    if query_text:
        conditions.append("(person_name LIKE ? OR sender_name LIKE ? OR recipient_name LIKE ? OR reference_number LIKE ? OR bank_name LIKE ? OR ocr_text LIKE ?)")
        q_pattern = f"%{query_text}%"
        params.extend([q_pattern, q_pattern, q_pattern, q_pattern, q_pattern, q_pattern])

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    # Sorting
    sort_map = {
        "date_desc": "occurred_at DESC, created_at DESC, id DESC",
        "date_asc": "occurred_at ASC, created_at ASC, id ASC",
        "amount_desc": "amount DESC, occurred_at DESC",
        "amount_asc": "amount ASC, occurred_at ASC",
        "created_desc": "created_at DESC, id DESC"
    }
    order_clause = sort_map.get(sort_by, "occurred_at DESC, created_at DESC")
    
    sql = f"SELECT * FROM transactions {where_clause} ORDER BY {order_clause} LIMIT ?"
    params.append(limit)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

def get_monthly_summary(year: int, month: int, workspace_id: str = None):
    """Calculates summary statistics for a given month with Decimal precision and workspace isolation."""
    month_str = f"{year:04d}-{month:02d}"
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT 
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions 
            WHERE strftime('%Y-%m', transaction_date) = ? 
              AND {ws_filter}
              AND deleted_at IS NULL
            GROUP BY transaction_type
        """, (month_str, ws_id))
        rows = cursor.fetchall()
        
        dec_sent = Decimal('0.00')
        dec_received = Decimal('0.00')
        tx_count = 0
        
        for r in rows:
            tx_count += r['count']
            amt = Decimal(str(r['total_amount'] or '0.00')).quantize(CENT)
            if r['transaction_type'] == 'SENT':
                dec_sent = amt
            elif r['transaction_type'] == 'RECEIVED':
                dec_received = amt
                
        # Top recipient (most money sent to)
        cursor.execute(f"""
            SELECT person_name, SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? 
              AND {ws_filter}
              AND transaction_type = 'SENT' AND person_name != '' AND deleted_at IS NULL
            GROUP BY person_name
            ORDER BY total DESC LIMIT 1
        """, (month_str, ws_id))
        top_sent_row = cursor.fetchone()
        top_recipient = dict(top_sent_row) if top_sent_row else None
        
        return {
            'year': year,
            'month': month,
            'total_sent': float(dec_sent),
            'total_received': float(dec_received),
            'net_savings': float(dec_received - dec_sent),
            'tx_count': tx_count,
            'top_recipient': top_recipient
        }

def get_transaction_by_id(tx_id: int, workspace_id: str = None):
    """Fetches a transaction by its ID (live only), optionally scoped to workspace with dual-read fallback."""
    default_ws = get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if workspace_id:
            cursor.execute(
                "SELECT * FROM transactions WHERE id = ? AND deleted_at IS NULL AND (workspace_id = ? OR workspace_id = ? OR workspace_id IS NULL)",
                (tx_id, workspace_id, default_ws)
            )
        else:
            cursor.execute("SELECT * FROM transactions WHERE id = ? AND deleted_at IS NULL", (tx_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_transaction_by_uid(uid: str, live_only: bool = False, workspace_id: str = None):
    """Fetches a transaction by its permanent UID (live or deleted, or live only if requested), optionally scoped to workspace with dual-read fallback."""
    if not uid:
        return None
    default_ws = get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        conditions = ["uid = ?"]
        params = [uid]
        if live_only:
            conditions.append("deleted_at IS NULL")
        if workspace_id:
            conditions.append("(workspace_id = ? OR workspace_id = ? OR workspace_id IS NULL)")
            params.extend([workspace_id, default_ws])
        where_clause = " AND ".join(conditions)
        cursor.execute(f"SELECT * FROM transactions WHERE {where_clause}", params)
        row = cursor.fetchone()
        return dict(row) if row else None

def get_live_transaction_by_uid(uid: str, workspace_id: str = None):
    """Fetches an active, non-deleted transaction by permanent UID, optionally scoped to workspace."""
    return get_transaction_by_uid(uid, live_only=True, workspace_id=workspace_id)

ALLOWED_UPDATE_COLUMNS = {
    'amount', 'transaction_type', 'person_name', 'sender_name', 'recipient_name',
    'payment_app', 'transaction_date', 'transaction_time', 'bank_name',
    'reference_number', 'category', 'raw_text', 'payment_status', 'note',
    'occurred_at', 'confidence', 'ocr_text'
}

def update_transaction(tx_id: int, updates: dict, workspace_id: str = None) -> bool:
    """Updates specific fields of an active transaction with validation, occurred_at recalculation, and thread locking."""
    if not updates:
        return False

    # Enforce whitelist of allowed columns (UID is strictly immutable)
    for key in updates.keys():
        if key not in ALLOWED_UPDATE_COLUMNS:
            raise ValueError(f"Disallowed column in updates: '{key}'. Allowed columns are: {sorted(ALLOWED_UPDATE_COLUMNS)}")

    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection

        validated_updates = dict(updates)
        if 'amount' in validated_updates:
            validated_updates['amount'] = float(parse_decimal_amount(validated_updates['amount'], allow_zero=False))
        if 'transaction_type' in validated_updates:
            validated_updates['transaction_type'] = validate_transaction_type(validated_updates['transaction_type'])
        if 'person_name' in validated_updates:
            validated_updates['person_name'] = validate_string_length(validated_updates['person_name'], max_length=120, field_name="Person name")
        if 'sender_name' in validated_updates:
            validated_updates['sender_name'] = validate_string_length(validated_updates['sender_name'], max_length=120, field_name="Sender name")
        if 'recipient_name' in validated_updates:
            validated_updates['recipient_name'] = validate_string_length(validated_updates['recipient_name'], max_length=120, field_name="Recipient name")
        if 'reference_number' in validated_updates:
            validated_updates['reference_number'] = validate_string_length(validated_updates['reference_number'], max_length=100, field_name="Reference number")
        if 'category' in validated_updates:
            validated_updates['category'] = validate_string_length(validated_updates['category'], max_length=100, field_name="Category")
            
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Ensure the row is active/live (deleted_at IS NULL)
            cursor.execute("SELECT id, transaction_date, transaction_time, workspace_id FROM transactions WHERE id = ? AND deleted_at IS NULL", (tx_id,))
            cur_row = cursor.fetchone()
            if not cur_row:
                return False

            default_ws = get_default_workspace_id()
            if workspace_id and cur_row['workspace_id'] and cur_row['workspace_id'] != workspace_id and cur_row['workspace_id'] != default_ws:
                return False

            ws_id = cur_row['workspace_id'] or workspace_id or default_ws

            # If transaction_date or transaction_time changed, recalculate occurred_at
            if 'transaction_date' in validated_updates or 'transaction_time' in validated_updates:
                new_date = validated_updates.get('transaction_date', cur_row['transaction_date'])
                new_time = validated_updates.get('transaction_time', cur_row['transaction_time'])
                validated_updates['occurred_at'] = build_occurred_at(new_date, new_time)

            now_utc = utc_now_iso()
            validated_updates['updated_at'] = now_utc

            set_clause = ", ".join([f"{k} = ?" for k in validated_updates.keys()])
            values = list(validated_updates.values()) + [tx_id]
            cursor.execute(f"UPDATE transactions SET {set_clause} WHERE id = ? AND deleted_at IS NULL", values)
            success = cursor.rowcount > 0

            # If balance-impacting fields changed, recalculate the chain in this connection
            balance_fields = {'amount', 'transaction_type', 'transaction_date', 'transaction_time', 'occurred_at'}
            if any(f in validated_updates for f in balance_fields):
                recalculate_in_connection(conn, workspace_id=ws_id)

            if success:
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return success

def delete_transaction(tx_id: int, workspace_id: str = None) -> bool:
    """
    Soft-deletes a transaction by setting deleted_at and updated_at using utc_now_iso().
    Preserves permanent uid and recalculates balance chain over remaining live rows
    all in one database transaction under LEDGER_LOCK.
    Does NOT purge tombstones inside the delete path.
    Does NOT resequence transaction IDs.
    """
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT workspace_id FROM transactions WHERE id = ? AND deleted_at IS NULL", (tx_id,))
            row = cursor.fetchone()
            if not row:
                return False
            default_ws = get_default_workspace_id()
            if workspace_id and row['workspace_id'] and row['workspace_id'] != workspace_id and row['workspace_id'] != default_ws:
                return False
            ws_id = row['workspace_id'] or workspace_id or default_ws

            cursor.execute(
                "UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now_utc, now_utc, tx_id)
            )
            deleted = (cursor.rowcount == 1)

            if deleted:
                recalculate_in_connection(conn, workspace_id=ws_id)
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return deleted

def delete_transaction_by_uid(uid: str, workspace_id: str = None) -> bool:
    """
    Soft-deletes a transaction by permanent uid under LEDGER_LOCK in one database transaction.
    Recalculates balance chain over live rows. Does not purge tombstones.
    """
    if not uid:
        return False
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection
        now_utc = utc_now_iso()
        valid_uid = validate_uid(uid)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT workspace_id FROM transactions WHERE uid = ? AND deleted_at IS NULL", (valid_uid,))
            row = cursor.fetchone()
            if not row:
                return False
            default_ws = get_default_workspace_id()
            if workspace_id and row['workspace_id'] and row['workspace_id'] != workspace_id and row['workspace_id'] != default_ws:
                return False
            ws_id = row['workspace_id'] or workspace_id or default_ws

            cursor.execute(
                "UPDATE transactions SET deleted_at = ?, updated_at = ? WHERE uid = ? AND deleted_at IS NULL",
                (now_utc, now_utc, valid_uid)
            )
            deleted = (cursor.rowcount == 1)

            if deleted:
                recalculate_in_connection(conn, workspace_id=ws_id)
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return deleted

def restore_soft_deleted_transaction(uid: str = None, tx_id: int = None) -> bool:
    """
    Restores a soft-deleted transaction by setting deleted_at = NULL and updated_at = utc_now_iso()
    WHERE uid = ? AND deleted_at IS NOT NULL.
    Returns success only if exactly one row changed, then recalculates the chain under LEDGER_LOCK.
    """
    if not uid and not tx_id:
        return False
    with LEDGER_LOCK:
        from services.balance_service import recalculate_in_connection
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            target_uid = uid
            if not target_uid and tx_id:
                cursor.execute("SELECT uid, workspace_id FROM transactions WHERE id = ?", (tx_id,))
                row = cursor.fetchone()
                if not row or not row['uid']:
                    return False
                target_uid = row['uid']
                ws_id = row['workspace_id'] or get_default_workspace_id()
            else:
                cursor.execute("SELECT workspace_id FROM transactions WHERE uid = ?", (target_uid,))
                row = cursor.fetchone()
                ws_id = (row['workspace_id'] if row else None) or get_default_workspace_id()

            valid_uid = validate_uid(target_uid)
            cursor.execute(
                "UPDATE transactions SET deleted_at = NULL, updated_at = ? WHERE uid = ? AND deleted_at IS NOT NULL",
                (now_utc, valid_uid)
            )
            restored = (cursor.rowcount == 1)

            if restored:
                recalculate_in_connection(conn, workspace_id=ws_id)
                increment_revision_and_mark_dirty(conn)

            conn.commit()
            return restored

def update_balance_setting(new_balance: float, workspace_id: str = None):
    """Updates the current balance in workspace_settings and settings under LEDGER_LOCK."""
    with LEDGER_LOCK:
        dec_bal = parse_decimal_amount(new_balance, allow_zero=True) if new_balance >= 0 else round(float(new_balance), 2)
        bal_str = str(float(dec_bal)) if hasattr(dec_bal, '__float__') else str(round(float(new_balance), 2))
        ws_id = workspace_id or get_default_workspace_id()
        now_utc = utc_now_iso()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('current_balance', ?, ?)", (bal_str, now_utc))
            cursor.execute("INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, 'current_balance', ?, ?)", (ws_id, bal_str, now_utc))
            increment_revision_and_mark_dirty(conn)
            conn.commit()

def get_balance_setting(workspace_id: str = None) -> float:
    """Gets the current balance from workspace_settings (with settings fallback)."""
    val = get_workspace_setting(workspace_id, 'current_balance')
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

def get_budget_setting(workspace_id: str = None) -> float:
    """Gets the monthly budget limit from workspace_settings (with settings fallback)."""
    val = get_workspace_setting(workspace_id, 'monthly_budget')
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

def set_budget_setting(amount: float, workspace_id: str = None):
    """Sets the monthly budget limit in workspace_settings and settings under LEDGER_LOCK."""
    dec_amount = parse_decimal_amount(amount, allow_zero=True)
    set_workspace_setting(workspace_id, 'monthly_budget', str(float(dec_amount)))

def get_model_setting() -> str:
    """Gets the user-selected preferred Gemini model from settings ('AUTO' by default)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'preferred_gemini_model'")
        row = cursor.fetchone()
        return str(row['value']).strip() if row and row['value'] else "AUTO"

def set_model_setting(model_name: str) -> None:
    """Persists the preferred Gemini model in settings under LEDGER_LOCK."""
    with LEDGER_LOCK:
        clean_model = (model_name or "AUTO").strip()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('preferred_gemini_model', ?, ?)",
                (clean_model, utc_now_iso())
            )
            conn.commit()

def get_monthly_spending(year: int, month: int, workspace_id: str = None) -> float:
    """Gets the total SENT amount for a given month with workspace isolation."""
    month_str = f"{year:04d}-{month:02d}"
    ws_id = workspace_id or get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(amount) as total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? 
              AND (workspace_id = ? OR workspace_id IS NULL)
              AND transaction_type = 'SENT' AND deleted_at IS NULL
        """, (month_str, ws_id))
        row = cursor.fetchone()
        return float(row['total']) if (row and row['total'] is not None) else 0.0

def get_category_summary(year: int, month: int, workspace_id: str = None):
    """Gets breakdown of spending (SENT) and income (RECEIVED) by category for a month with workspace isolation."""
    month_str = f"{year:04d}-{month:02d}"
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT 
                category,
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? 
              AND {ws_filter}
              AND deleted_at IS NULL
            GROUP BY category, transaction_type
            ORDER BY total_amount DESC
        """, (month_str, ws_id))
        return [dict(row) for row in cursor.fetchall()]

def get_daily_summary_stats(target_date_str: str, workspace_id: str = None):
    """Calculates summary statistics for a specific date (YYYY-MM-DD) with Decimal precision and workspace isolation."""
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT 
                transaction_type,
                COUNT(*) as count,
                SUM(amount) as total_amount
            FROM transactions 
            WHERE transaction_date = ? 
              AND {ws_filter}
              AND deleted_at IS NULL
            GROUP BY transaction_type
        """, (str(target_date_str), ws_id))
        rows = cursor.fetchall()
        
        dec_sent = Decimal('0.00')
        dec_received = Decimal('0.00')
        tx_count = 0
        
        for r in rows:
            tx_count += r['count']
            amt = Decimal(str(r['total_amount'] or '0.00')).quantize(CENT)
            if r['transaction_type'] == 'SENT':
                dec_sent = amt
            elif r['transaction_type'] == 'RECEIVED':
                dec_received = amt
                
        # Get list of transactions for the day
        cursor.execute(f"""
            SELECT id, transaction_type, amount, person_name, category, payment_app, transaction_time, balance_after
            FROM transactions
            WHERE transaction_date = ? 
              AND {ws_filter}
              AND deleted_at IS NULL
            ORDER BY id ASC
        """, (str(target_date_str), ws_id))
        transactions = [dict(row) for row in cursor.fetchall()]
        
        return {
            'date': str(target_date_str),
            'total_sent': float(dec_sent),
            'total_received': float(dec_received),
            'net_change': float(dec_received - dec_sent),
            'tx_count': tx_count,
            'transactions': transactions
        }

def get_cafeteria_transactions(limit: int = 50, workspace_id: str = None):
    """Fetches transactions related to Vikraman Nair / Cafeteria with workspace isolation."""
    ws_id = workspace_id or get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM transactions 
            WHERE (lower(person_name) LIKE '%vikraman%' 
               OR lower(person_name) LIKE '%cafeteria%' 
               OR lower(person_name) LIKE '%canteen%'
               OR lower(upi_id) LIKE '%vikraman%')
               AND (workspace_id = ? OR workspace_id IS NULL)
               AND deleted_at IS NULL
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
        """, (ws_id, limit))
        return [dict(row) for row in cursor.fetchall()]


# --- Payee Category Memory ---

def get_payee_category(payee_name: str, workspace_id: str = None) -> str | None:
    """Retrieves remembered category for a payee if available with workspace isolation."""
    if not payee_name or not payee_name.strip():
        return None
    normalized = payee_name.strip().lower()
    ws_id = workspace_id or get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT category FROM payee_categories 
            WHERE lower(payee_name) = ? AND (workspace_id = ? OR workspace_id IS NULL)
            ORDER BY updated_at DESC LIMIT 1
        """, (normalized, ws_id))
        row = cursor.fetchone()
        if row:
            return row['category']
        # Also check existing transactions history as fallback
        cursor.execute("""
            SELECT category FROM transactions 
            WHERE lower(person_name) = ? 
              AND (workspace_id = ? OR workspace_id IS NULL)
              AND category IS NOT NULL AND category != 'General' AND deleted_at IS NULL
            ORDER BY id DESC LIMIT 1
        """, (normalized, ws_id))
        t_row = cursor.fetchone()
        return t_row['category'] if t_row else None

def remember_payee_category(payee_name: str, category: str, workspace_id: str = None):
    """Upserts payee -> category preference in payee_categories with workspace isolation."""
    if not payee_name or not category or not payee_name.strip():
        return
    normalized = payee_name.strip().lower()
    ws_id = workspace_id or get_default_workspace_id()
    now_utc = utc_now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO payee_categories (workspace_id, payee_name, category, updated_at)
            VALUES (?, ?, ?, ?)
        """, (ws_id, normalized, category, now_utc))
        conn.commit()


# --- Duplicate Detection ---

def find_potential_duplicate(
    amount: float,
    reference_number: str = None,
    person_name: str = None,
    tx_date: str = None,
    workspace_id: str = None
):
    """Checks if a similar transaction already exists within the workspace (by reference number or amount/payee/date) with dual-read fallback."""
    ws_id = workspace_id or get_default_workspace_id()
    default_ws = get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check by reference number first (strongest indicator)
        if reference_number and len(str(reference_number).strip()) > 3:
            clean_ref = str(reference_number).strip()
            cursor.execute("""
                SELECT * FROM transactions 
                WHERE reference_number = ? 
                  AND (workspace_id = ? OR workspace_id = ? OR workspace_id IS NULL)
                  AND deleted_at IS NULL 
                ORDER BY id DESC LIMIT 1
            """, (clean_ref, ws_id, default_ws))
            row = cursor.fetchone()
            if row:
                res = dict(row)
                res['match_reason'] = f"same ref …{clean_ref[-4:]}"
                return res
        
        # Check by amount and person within same date or near date
        if amount and amount > 0:
            if person_name and person_name.strip():
                clean_person = person_name.strip().lower()
                if tx_date:
                    cursor.execute("""
                        SELECT * FROM transactions 
                        WHERE abs(amount - ?) < 0.01 
                          AND lower(person_name) LIKE ?
                          AND abs(julianday(transaction_date) - julianday(?)) <= 2
                          AND (workspace_id = ? OR workspace_id = ? OR workspace_id IS NULL)
                          AND deleted_at IS NULL
                        ORDER BY id DESC LIMIT 1
                    """, (float(amount), f"%{clean_person}%", tx_date, ws_id, default_ws))
                else:
                    cursor.execute("""
                        SELECT * FROM transactions 
                        WHERE abs(amount - ?) < 0.01 
                          AND lower(person_name) LIKE ?
                          AND (workspace_id = ? OR workspace_id = ? OR workspace_id IS NULL)
                          AND deleted_at IS NULL
                        ORDER BY id DESC LIMIT 1
                    """, (float(amount), f"%{clean_person}%", ws_id, default_ws))
                row = cursor.fetchone()
                if row:
                    res = dict(row)
                    res['match_reason'] = f"same amount ₹{amount:.0f} to {res.get('person_name')}"
                    return res
        return None


# --- Top Payees & Daily Series ---

def get_top_payees(limit: int = 5, year: int = None, month: int = None, workspace_id: str = None):
    """Fetches top payees by total spent with workspace isolation."""
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = f"""
            SELECT 
                person_name,
                SUM(amount) as total_amount,
                COUNT(*) as count,
                MAX(category) as primary_category
            FROM transactions
            WHERE transaction_type = 'SENT' 
              AND person_name IS NOT NULL 
              AND TRIM(person_name) != ''
              AND person_name != 'Unknown'
              AND {ws_filter}
              AND deleted_at IS NULL
        """
        params = [ws_id]
        if year and month:
            query += " AND strftime('%Y-%m', transaction_date) = ?"
            params.append(f"{year:04d}-{month:02d}")
        elif year:
            query += " AND strftime('%Y', transaction_date) = ?"
            params.append(str(year))
            
        query += " GROUP BY person_name ORDER BY total_amount DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def get_daily_spend_series(year: int, month: int, workspace_id: str = None):
    """Returns daily spending and income series for a month for charts and heatmaps with workspace isolation."""
    month_str = f"{year:04d}-{month:02d}"
    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT 
                transaction_date as date,
                SUM(CASE WHEN transaction_type = 'SENT' THEN amount ELSE 0 END) as spent,
                SUM(CASE WHEN transaction_type = 'RECEIVED' THEN amount ELSE 0 END) as received,
                COUNT(*) as count
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ? 
              AND {ws_filter}
              AND deleted_at IS NULL
            GROUP BY transaction_date
            ORDER BY transaction_date ASC
        """, (month_str, ws_id))
        return [dict(row) for row in cursor.fetchall()]

def get_month_comparison_stats(year: int, month: int, workspace_id: str = None):
    """Calculates current month totals and previous month totals for month-over-month deltas with workspace isolation."""
    current_summary = get_monthly_summary(year, month, workspace_id=workspace_id)
    
    # Previous month calculation
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1
        
    prev_summary = get_monthly_summary(prev_year, prev_month, workspace_id=workspace_id)
    
    def calc_delta(curr, prev):
        if prev > 0:
            pct = ((curr - prev) / prev) * 100
            diff = curr - prev
            return {'diff': diff, 'pct': round(pct, 1), 'direction': 'up' if diff > 0 else ('down' if diff < 0 else 'flat')}
        elif curr > 0:
            return {'diff': curr, 'pct': 100.0, 'direction': 'up'}
        return {'diff': 0.0, 'pct': 0.0, 'direction': 'flat'}

    return {
        'current': current_summary,
        'previous': prev_summary,
        'prev_period': f"{prev_year:04d}-{prev_month:02d}",
        'spent_delta': calc_delta(current_summary['total_sent'], prev_summary['total_sent']),
        'received_delta': calc_delta(current_summary['total_received'], prev_summary['total_received']),
        'net_delta': calc_delta(current_summary['net_savings'], prev_summary['net_savings'])
    }

def get_transactions_paginated(
    page: int = 1,
    page_size: int = 25,
    search: str = None,
    category: str = None,
    tx_type: str = None,
    transaction_type: str = None,
    year: int = None,
    month: int = None,
    sort_by: str = "date_desc",
    workspace_id: str = None
):
    """Fetches paginated transactions with optional filters, customizable sorting, safe bounds, and workspace isolation."""
    try:
        page = max(1, int(page or 1))
    except (ValueError, TypeError):
        page = 1
        
    try:
        page_size = max(1, min(int(page_size or 25), 200))
    except (ValueError, TypeError):
        page_size = 25

    default_ws = get_default_workspace_id()
    ws_id = workspace_id or default_ws
    ws_filter = "(workspace_id = ? OR workspace_id IS NULL)" if ws_id == default_ws else "workspace_id = ?"
    conditions = [ws_filter, "deleted_at IS NULL"]
    params = [ws_id]
    
    if search and search.strip():
        clean_search = search.strip()[:100]
        s = f"%{clean_search}%"
        conditions.append("(person_name LIKE ? OR category LIKE ? OR reference_number LIKE ? OR payment_app LIKE ?)")
        params.extend([s, s, s, s])
        
    if category and category.strip() and category.lower() != 'all':
        conditions.append("category = ?")
        params.append(category.strip()[:100])
        
    effective_type = (tx_type or transaction_type or "").strip().upper()
    if effective_type and effective_type != "ALL":
        if effective_type in ('SENT', 'RECEIVED', 'TRANSFER'):
            conditions.append("transaction_type = ?")
            params.append(effective_type)
        else:
            raise ValueError(f"Invalid transaction type filter: '{effective_type}'")
        
    if year and month:
        if not (1900 <= int(year) <= 2200):
            raise ValueError(f"Year out of range: {year}")
        if not (1 <= int(month) <= 12):
            raise ValueError(f"Month out of range: {month}")
        conditions.append("strftime('%Y-%m', transaction_date) = ?")
        params.append(f"{int(year):04d}-{int(month):02d}")
    elif year:
        if not (1900 <= int(year) <= 2200):
            raise ValueError(f"Year out of range: {year}")
        conditions.append("strftime('%Y', transaction_date) = ?")
        params.append(str(int(year)))
    elif month:
        if not (1 <= int(month) <= 12):
            raise ValueError(f"Month out of range: {month}")
        conditions.append("strftime('%m', transaction_date) = ?")
        params.append(f"{int(month):02d}")
        
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    sort_map = {
        "date_desc": "occurred_at DESC, created_at DESC, id DESC",
        "date_asc": "occurred_at ASC, created_at ASC, id ASC",
        "id_desc": "id DESC, created_at DESC",
        "id_asc": "id ASC, created_at ASC",
        "created_desc": "created_at DESC, id DESC",
        "created_asc": "created_at ASC, id ASC",
        "amount_desc": "amount DESC, occurred_at DESC",
        "amount_asc": "amount ASC, occurred_at ASC",
    }
    order_clause = sort_map.get((sort_by or "date_desc").lower(), "occurred_at DESC, created_at DESC, id DESC")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Count total matching
        cursor.execute(f"SELECT COUNT(*) as total FROM transactions {where_clause}", params)
        total_count = cursor.fetchone()['total']
        
        # Paginated items
        offset = (page - 1) * page_size
        paginated_params = params + [page_size, offset]
        cursor.execute(f"""
            SELECT * FROM transactions 
            {where_clause}
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
        """, paginated_params)
        items = [dict(r) for r in cursor.fetchall()]
        
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        return {
            'transactions': items,
            'total_count': total_count,
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages
        }

def get_contact_ledger(workspace_id: str = None):
    """Aggregates all transactions by contact/payee with workspace isolation."""
    ws_id = workspace_id or get_default_workspace_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                person_name,
                COUNT(*) as total_transactions,
                SUM(CASE WHEN transaction_type = 'SENT' THEN amount ELSE 0 END) as total_sent,
                SUM(CASE WHEN transaction_type = 'RECEIVED' THEN amount ELSE 0 END) as total_received,
                MAX(transaction_date) as last_transaction_date,
                MAX(category) as primary_category
            FROM transactions
            WHERE person_name IS NOT NULL AND TRIM(person_name) != '' AND person_name != 'Unknown' 
              AND (workspace_id = ? OR workspace_id IS NULL)
              AND deleted_at IS NULL
            GROUP BY lower(TRIM(person_name))
            ORDER BY (SUM(CASE WHEN transaction_type = 'SENT' THEN amount ELSE 0 END) + SUM(CASE WHEN transaction_type = 'RECEIVED' THEN amount ELSE 0 END)) DESC
        """, (ws_id,))
        rows = cursor.fetchall()
        contacts = []
        for r in rows:
            sent = float(r['total_sent'] or 0.0)
            received = float(r['total_received'] or 0.0)
            contacts.append({
                'name': r['person_name'],
                'tx_count': r['total_transactions'],
                'total_sent': sent,
                'total_received': received,
                'net_balance': received - sent, # Positive means they gave you more than you gave them
                'last_date': r['last_transaction_date'],
                'category': r['primary_category'] or 'General'
            })
        return contacts


def save_pending_receipt(pending_id: str, transaction, workspace_id: Optional[str] = None) -> None:
    """Persists a pending receipt transaction in SQLite so it survives bot reboots/restarts."""
    import json
    from datetime import date, datetime

    ws_id = workspace_id or getattr(transaction, 'workspace_id', None) or get_default_workspace_id()

    d = {}
    for k, v in transaction.__dict__.items():
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
        else:
            d[k] = v
    if ws_id:
        d['workspace_id'] = ws_id

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO pending_receipts (pending_id, data_json, created_at, workspace_id) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
            (pending_id, json.dumps(d), ws_id)
        )
        cursor.execute("DELETE FROM pending_receipts WHERE created_at < datetime('now', '-7 days')")
        conn.commit()


def get_pending_receipt(pending_id: str, workspace_id: Optional[str] = None):
    """Retrieves a persisted pending receipt transaction from SQLite with dual-read fallback."""
    import json
    from datetime import date, datetime
    from database.models import Transaction

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if workspace_id:
            cursor.execute(
                "SELECT data_json, workspace_id FROM pending_receipts WHERE pending_id = ? AND (workspace_id = ? OR workspace_id IS NULL)",
                (pending_id, workspace_id)
            )
        else:
            cursor.execute("SELECT data_json, workspace_id FROM pending_receipts WHERE pending_id = ?", (pending_id,))
        row = cursor.fetchone()
        if not row:
            return None

        try:
            d = json.loads(row['data_json'])
            t = Transaction()
            for k, v in d.items():
                if hasattr(t, k):
                    if k == 'transaction_date' and v and isinstance(v, str):
                        try:
                            setattr(t, k, date.fromisoformat(v[:10]))
                        except Exception:
                            setattr(t, k, None)
                    elif k in ('deleted_at', 'created_at', 'updated_at') and v and isinstance(v, str):
                        try:
                            setattr(t, k, datetime.fromisoformat(v))
                        except Exception:
                            setattr(t, k, None)
                    else:
                        setattr(t, k, v)
            if hasattr(t, 'workspace_id') and not t.workspace_id:
                t.workspace_id = row['workspace_id'] or workspace_id
            return t
        except Exception as e:
            logger.error(f"Error parsing pending receipt {pending_id}: {e}")
            return None


def delete_pending_receipt(pending_id: str, workspace_id: Optional[str] = None) -> None:
    """Deletes a pending receipt transaction from SQLite."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if workspace_id:
            cursor.execute(
                "DELETE FROM pending_receipts WHERE pending_id = ? AND (workspace_id = ? OR workspace_id IS NULL)",
                (pending_id, workspace_id)
            )
        else:
            cursor.execute("DELETE FROM pending_receipts WHERE pending_id = ?", (pending_id,))
        conn.commit()


def migrate_workspace_chat_id(old_chat_id: int | str, new_chat_id: int | str) -> bool:
    """
    Handles Telegram group -> supergroup chat migration.
    Updates the workspace's chat_id and sets chat_type to 'supergroup'
    while preserving all transactions, memberships, and configurations.
    """
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, chat_id, title FROM workspaces WHERE chat_id = ?", (int(old_chat_id),))
            ws = cursor.fetchone()
            if not ws:
                logger.warning(f"Workspace with chat_id={old_chat_id} not found for migration to {new_chat_id}")
                return False
            now_utc = utc_now_iso()
            cursor.execute(
                "UPDATE workspaces SET chat_id = ?, chat_type = 'supergroup', updated_at = ? WHERE chat_id = ?",
                (int(new_chat_id), now_utc, int(old_chat_id))
            )
            # Update any chat_id references in undo_log
            cursor.execute("UPDATE undo_log SET chat_id = ? WHERE chat_id = ?", (int(new_chat_id), int(old_chat_id)))
            conn.commit()
            from utils.telemetry import increment_metric
            increment_metric("chat_migrations")
            logger.info(f"Successfully migrated workspace {ws['id']} ('{ws['title']}') from chat_id {old_chat_id} to {new_chat_id}")
            return True


def get_active_workspaces() -> list:
    """Returns all active workspaces."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id, chat_type, title, is_active, created_at, updated_at FROM workspaces WHERE is_active = 1")
        return [RowDict(row) for row in cursor.fetchall()]




