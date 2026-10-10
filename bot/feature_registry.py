"""
Feature Access Registry & Matrix for Role-Based Permissions (Fix 11).

Defines workspace features, default role permissions (admin, member, viewer),
database persistence in feature_permissions table, and central enforcement via feature_allowed().
"""
from typing import Dict, List, Optional, Set, Tuple
import logging
from database.db import get_db_connection, LEDGER_LOCK
from database.queries import utc_now_iso

logger = logging.getLogger(__name__)

# Canonical feature registry: (feature_key, label, default_roles)
FEATURES: List[Tuple[str, str, Set[str]]] = [
    ("history",     "View history",           {"admin", "member", "viewer"}),
    ("monthly",     "Monthly view",           {"admin", "member", "viewer"}),
    ("details",     "Transaction details",    {"admin", "member", "viewer"}),
    ("report",      "Reports",                {"admin", "member"}),
    ("statement",   "Statement",              {"admin", "member"}),
    ("export",      "Export data",            {"admin", "member"}),
    ("record",      "Record transactions",    {"admin", "member"}),
    ("edit_own",    "Edit own transactions",  {"admin", "member"}),
    ("delete_own",  "Delete own transactions",{"admin", "member"}),
    ("budget",      "Set budgets",            {"admin"}),
    ("backup",      "Manual backup",          {"admin"}),
    ("invite",      "Invite / approve users", {"admin"}),
    ("recurring",   "Recurring payments",     {"admin"}),
    ("month_close", "Close/reopen month",     {"admin"}),
    ("cafe",        "Cafeteria menu",         {"admin"}),
]

ROLES = ["admin", "member", "viewer"]

def get_feature_by_key(feature_key: str) -> Optional[Tuple[str, str, Set[str]]]:
    for f in FEATURES:
        if f[0] == feature_key:
            return f
    return None

def init_feature_permissions_table(conn) -> None:
    """Creates the feature_permissions table if not present and backfills existing workspaces."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feature_permissions (
            workspace_id TEXT NOT NULL,
            feature_key  TEXT NOT NULL,
            role         TEXT NOT NULL,
            allowed      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (workspace_id, feature_key, role)
        )
    """)
    # Seed default permissions for any active workspace lacking rows
    cursor.execute("SELECT id FROM workspaces WHERE is_active = 1")
    ws_rows = cursor.fetchall()
    for row in ws_rows:
        ws_id = row["id"]
        cursor.execute("SELECT COUNT(*) FROM feature_permissions WHERE workspace_id = ?", (ws_id,))
        if cursor.fetchone()[0] == 0:
            seed_default_feature_permissions(ws_id, conn=conn)

def seed_default_feature_permissions(workspace_id: str, conn=None) -> None:
    """Seeds canonical default permissions for a workspace."""
    if not workspace_id:
        return

    def _seed(c):
        for feat_key, _, def_roles in FEATURES:
            for role in ROLES:
                allowed_int = 1 if role in def_roles else 0
                c.execute("""
                    INSERT OR IGNORE INTO feature_permissions (workspace_id, feature_key, role, allowed)
                    VALUES (?, ?, ?, ?)
                """, (workspace_id, feat_key, role, allowed_int))

    if conn is not None:
        _seed(conn.cursor())
    else:
        with LEDGER_LOCK:
            with get_db_connection() as c_conn:
                _seed(c_conn.cursor())
                c_conn.commit()

def lookup_feature_permission(workspace_id: str, feature_key: str, role: str) -> Optional[bool]:
    """Look up whether a specific role is allowed for a feature in a workspace."""
    if not workspace_id or not feature_key or not role:
        return None
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT allowed FROM feature_permissions
                WHERE workspace_id = ? AND feature_key = ? AND role = ?
            """, (str(workspace_id), str(feature_key), str(role).lower()))
            row = cursor.fetchone()
            if row is not None:
                return bool(row["allowed"])
    except Exception as e:
        logger.debug(f"lookup_feature_permission error: {e}")
    return None

def set_feature_permission(workspace_id: str, feature_key: str, role: str, allowed: bool) -> bool:
    """Sets a feature permission cell in the matrix."""
    if not workspace_id or not feature_key or not role:
        return False
    role_norm = str(role).lower()
    if role_norm not in ROLES:
        return False
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO feature_permissions (workspace_id, feature_key, role, allowed)
                VALUES (?, ?, ?, ?)
            """, (str(workspace_id), str(feature_key), role_norm, 1 if allowed else 0))
            conn.commit()
    return True

def reset_feature_permissions(workspace_id: str, feature_key: Optional[str] = None) -> None:
    """Resets feature permissions to canonical defaults for one feature or all features."""
    if not workspace_id:
        return
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if feature_key:
                cursor.execute("DELETE FROM feature_permissions WHERE workspace_id = ? AND feature_key = ?", (str(workspace_id), str(feature_key)))
                feat = get_feature_by_key(feature_key)
                if feat:
                    for role in ROLES:
                        allowed_int = 1 if role in feat[2] else 0
                        cursor.execute("""
                            INSERT OR REPLACE INTO feature_permissions (workspace_id, feature_key, role, allowed)
                            VALUES (?, ?, ?, ?)
                        """, (str(workspace_id), feature_key, role, allowed_int))
            else:
                cursor.execute("DELETE FROM feature_permissions WHERE workspace_id = ?", (str(workspace_id),))
                seed_default_feature_permissions(workspace_id, conn=conn)
            conn.commit()

def get_workspace_feature_matrix(workspace_id: str) -> Dict[str, Dict[str, bool]]:
    """Returns a full mapping of feature_key -> {role: allowed_bool} for a workspace."""
    matrix = {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT feature_key, role, allowed FROM feature_permissions WHERE workspace_id = ?", (str(workspace_id),))
        for r in cursor.fetchall():
            f_k = r["feature_key"]
            if f_k not in matrix:
                matrix[f_k] = {}
            matrix[f_k][r["role"]] = bool(r["allowed"])
    
    # Fill in any missing keys from defaults
    for f_k, _, def_roles in FEATURES:
        if f_k not in matrix:
            matrix[f_k] = {}
        for role in ROLES:
            if role not in matrix[f_k]:
                matrix[f_k][role] = (role in def_roles)
    return matrix

def feature_allowed(workspace_id: str, user_id: int, feature_key: str) -> bool:
    """
    Central choke-point for role permissions.
    - Global owner always bypasses (returns True).
    - Checks member role and looks up allowed flag in feature_permissions.
    """
    from bot.auth import is_global_owner
    if is_global_owner(user_id):
        return True
    if not workspace_id or user_id is None:
        return False
    from database.queries import get_workspace_member
    member = get_workspace_member(str(workspace_id), int(user_id))
    if not member or not member.is_active or getattr(member, 'status', 'active') != 'active':
        return False
    role = (member.role or 'viewer').lower()
    if role == 'owner':
        return True
    
    allowed = lookup_feature_permission(str(workspace_id), feature_key, role)
    if allowed is not None:
        return allowed

    feat = get_feature_by_key(feature_key)
    if feat:
        return role in feat[2]
    return False
