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
    actor_role: str = "member",
    request_id: str = "",
    result: str = "success",
    details: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Appends an immutable audit record for a tenant action.
    """
    if not workspace_id:
        return False

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
                    str(actor_role or "member"),
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
