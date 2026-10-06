"""
Workspace Storage and Directory Isolation Utility.
Provides directory isolation per tenant workspace to prevent path traversal and cross-tenant data leakage.
"""

import re
from pathlib import Path
from config import DATA_DIR, logger

# Strict alphanumeric/hyphen workspace ID pattern to prevent path traversal
_WORKSPACE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


def get_workspace_dir(workspace_id: str, subdir: str = "") -> Path:
    """
    Returns an isolated Path for a specific workspace, safely nested under DATA_DIR / 'workspaces'.
    Guarantees no directory traversal.
    """
    if not workspace_id or not _WORKSPACE_ID_REGEX.match(str(workspace_id)):
        safe_ws_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(workspace_id or "default"))
    else:
        safe_ws_id = str(workspace_id)

    base = (DATA_DIR / "workspaces" / safe_ws_id).resolve()
    
    # Ensure resolved path is strictly within DATA_DIR / 'workspaces'
    workspaces_root = (DATA_DIR / "workspaces").resolve()
    if workspaces_root not in base.parents and base != workspaces_root:
        raise ValueError(f"Directory traversal detected for workspace_id: {workspace_id}")

    if subdir:
        # Sanitize subdir to prevent traversal
        safe_sub = re.sub(r"[^a-zA-Z0-9_-]", "_", str(subdir))
        target = base / safe_sub
    else:
        target = base

    target.mkdir(parents=True, exist_ok=True)
    return target


def get_workspace_export_dir(workspace_id: str) -> Path:
    return get_workspace_dir(workspace_id, "exports")


def get_workspace_backup_dir(workspace_id: str) -> Path:
    return get_workspace_dir(workspace_id, "backups")


def get_workspace_image_dir(workspace_id: str) -> Path:
    return get_workspace_dir(workspace_id, "images")
