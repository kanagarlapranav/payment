"""
Audit Logging Service for Multi-Tenant Workspace Events.
Records immutable security and operational audit trails scoped to each workspace.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from database.db import get_db_connection, LEDGER_LOCK
from config import logger


def log_audit_event(
    workspace_id: str,
    actor_user_id: int,
    action: str,
    resource: str,
    actor_role: Optional[str] = None,
    request_id: str = "",
    result: str = "success",
    details: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Appends an immutable audit record for a tenant action.

    Args:
        workspace_id: Unique UUID string identifier of the target workspace.
        actor_user_id: Telegram user ID performing the action.
        action: Identifier for the executed operation (e.g., 'member_add', 'export').
        resource: Target entity or resource modified (e.g., 'workspace_member', 'ledger').
        actor_role: Role of the actor (e.g. 'owner', 'admin', 'member'). Auto-resolved if None.
        request_id: Optional correlation ID for tracing.
        result: Outcome of the action ('success', 'failure', 'denied').
        details: Optional JSON-serializable dictionary with supplemental event metadata.

    Returns:
        bool: True if audit record was successfully persisted, False otherwise.
    """
    if not workspace_id:
        return False

    if not actor_role and actor_user_id and workspace_id:
        try:
            from database.queries import get_workspace_member
            mem = get_workspace_member(str(workspace_id), int(actor_user_id))
            if mem and getattr(mem, "role", None):
                actor_role = mem.role
            else:
                from config import SUPER_ADMIN_IDS
                if int(actor_user_id) in SUPER_ADMIN_IDS:
                    actor_role = "owner"
        except Exception:
            pass

    resolved_role = str(actor_role or "member")

    now_utc = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details or {})

    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO audit_logs (
                        workspace_id, actor_user_id, actor_role,
                        action, resource, request_id, result,
                        details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(workspace_id),
                    int(actor_user_id or 0),
                    str(resolved_role),
                    str(action),
                    str(resource),
                    str(request_id or ""),
                    str(result or "success"),
                    details_str,
                    now_utc
                ))
                conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to record audit log in workspace={workspace_id}: {e}")
        return False


def get_workspace_audit_logs(workspace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Fetches the recent audit events for a given workspace.

    Args:
        workspace_id: Unique UUID string identifier of the target workspace.
        limit: Maximum number of audit records to retrieve (default: 50).

    Returns:
        List[Dict[str, Any]]: List of audit log records ordered from newest to oldest.
    """
    if not workspace_id:
        return []

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, workspace_id, actor_user_id, actor_role,
                       action, resource, request_id, result,
                       details_json, created_at
                FROM audit_logs
                WHERE workspace_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (str(workspace_id), limit))
            rows = cursor.fetchall()

        events = []
        for r in rows:
            d = dict(r)
            try:
                d['details'] = json.loads(d.get('details_json') or '{}')
            except Exception:
                d['details'] = {}
            events.append(d)
        return events
    except Exception as e:
        logger.error(f"Failed to fetch audit logs for workspace={workspace_id}: {e}")
        return []


# Canonical function aliases
record_audit_event = log_audit_event
list_audit_events = get_workspace_audit_logs
