#!/usr/bin/env python3
"""
One-time repair script for already-misplaced rows in Payment Tracker.

Scans `transactions` and `undo_log` for rows whose `workspace_id` does not match
the active workspace owning their originating chat (`telegram_chat_id` / `chat_id`).
Re-tags them to the provenance-matched workspace ONLY when exactly one active
workspace matches that chat id.

Dry-run by default; modifies rows only when --apply is specified.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional, Union

# Ensure root directory is on PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def repair_provenance(
    conn_or_db_path: Optional[Union[sqlite3.Connection, str, Path]] = None,
    apply: bool = False,
) -> dict[str, Any]:
    """
    Scans and repairs provenance for transactions and undo_log rows.

    Args:
        conn_or_db_path: sqlite3.Connection, path string, Path object, or None (defaults to config.DB_PATH).
        apply: If True, writes changes and commits. If False, performs dry-run.

    Returns:
        Structured dictionary summarizing scanned, re-tagged, and skipped counts.
    """
    close_conn = False
    if isinstance(conn_or_db_path, sqlite3.Connection):
        conn = conn_or_db_path
    else:
        if conn_or_db_path is None:
            from config import DB_PATH
            target_path = str(DB_PATH)
        else:
            target_path = str(conn_or_db_path)
        conn = sqlite3.connect(target_path)
        conn.row_factory = sqlite3.Row
        close_conn = True

    try:
        cursor = conn.cursor()

        # Check existing tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        if "workspaces" not in existing_tables:
            return {
                "success": False,
                "error": "workspaces table does not exist",
                "apply": apply,
                "transactions": {"scanned": 0, "retagged": 0, "skipped": {}},
                "undo_log": {"scanned": 0, "retagged": 0, "skipped": {}},
            }

        snapshot_path = None
        if apply:
            import shutil, datetime
            db_path = None
            if not isinstance(conn_or_db_path, sqlite3.Connection):
                db_path = str(conn_or_db_path) if conn_or_db_path is not None else str(target_path)
            else:
                try:
                    for row in cursor.execute("PRAGMA database_list").fetchall():
                        if row[1] == "main" and row[2]:
                            db_path = row[2]
                            break
                except Exception:
                    pass
            if db_path and os.path.exists(db_path):
                snapshot_path = f"{db_path}.pre-repair-{datetime.datetime.now():%Y%m%d-%H%M%S}.bak"
                shutil.copy2(db_path, snapshot_path)

        # Cache active workspaces by chat_id string
        cursor.execute("SELECT id, chat_id, title FROM workspaces WHERE is_active = 1")
        ws_by_chat: dict[str, list[dict[str, Any]]] = {}
        ws_id_to_title: dict[str, str] = {}
        active_ws_ids: set[str] = set()
        for w in cursor.fetchall():
            w_id = str(w["id"])
            active_ws_ids.add(w_id)
            ws_id_to_title[w_id] = str(w["title"] or "")
            c_val = str(w["chat_id"]).strip() if w["chat_id"] is not None else ""
            if c_val:
                ws_by_chat.setdefault(c_val, []).append(dict(w))

        # Query default_workspace_id from settings
        default_ws_id = ""
        if "settings" in existing_tables:
            cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
            d_row = cursor.fetchone()
            default_ws_id = str(d_row[0]).strip() if d_row and d_row[0] else ""

        # Query owner_id from config
        import config
        owner_id = int(getattr(config, 'TELEGRAM_USER_ID', 0) or 0)

        touched_workspaces: set[str] = set()

        summary: dict[str, Any] = {
            "success": True,
            "apply": apply,
            "snapshot_path": snapshot_path,
            "deactivated_stale_owner_workspaces": 0,
            "transactions": {
                "scanned": 0,
                "retagged": 0,
                "skipped": {
                    "already_matched": 0,
                    "origin_less": 0,
                    "no_matching_workspace": 0,
                    "ambiguous_matching_workspaces": 0,
                },
                "details": [],
            },
            "undo_log": {
                "scanned": 0,
                "retagged": 0,
                "skipped": {
                    "already_matched": 0,
                    "origin_less": 0,
                    "no_matching_workspace": 0,
                    "ambiguous_matching_workspaces": 0,
                },
                "details": [],
            },
        }

        # 1. Inspect transactions
        if "transactions" in existing_tables:
            cursor.execute("PRAGMA table_info(transactions)")
            tx_cols = {r[1] for r in cursor.fetchall()}
            has_uid_col = "telegram_user_id" in tx_cols

            if has_uid_col:
                cursor.execute("SELECT id, workspace_id, telegram_chat_id, telegram_user_id FROM transactions ORDER BY id ASC")
            else:
                cursor.execute("SELECT id, workspace_id, telegram_chat_id, NULL as telegram_user_id FROM transactions ORDER BY id ASC")
            tx_rows = cursor.fetchall()
            summary["transactions"]["scanned"] = len(tx_rows)

            for tx in tx_rows:
                tx_id = tx["id"]
                current_ws = str(tx["workspace_id"]).strip() if tx["workspace_id"] else ""
                origin_chat = str(tx["telegram_chat_id"]).strip() if tx["telegram_chat_id"] is not None else ""
                user_id_raw = tx["telegram_user_id"]
                user_id_str = str(user_id_raw).strip() if user_id_raw is not None else ""
                user_id_int = None
                if user_id_str:
                    try:
                        user_id_int = int(user_id_str)
                    except ValueError:
                        pass

                target_ws = None
                target_title = ""
                reason = None

                # Pass 1: Global owner rows ALWAYS go home to default workspace (Fix 8 step 1)
                if (owner_id and user_id_int == owner_id) or (origin_chat and origin_chat == str(owner_id)):
                    if default_ws_id and default_ws_id in active_ws_ids:
                        target_ws = default_ws_id
                        target_title = ws_id_to_title.get(default_ws_id, "Default Workspace")
                        reason = "owner_default"
                    else:
                        reason = "skipped:no_default_workspace"
                # Pass 2: telegram_chat_id exact-match against active workspaces
                elif origin_chat:
                    matching = ws_by_chat.get(origin_chat, [])
                    if len(matching) == 1:
                        target_ws = matching[0]["id"]
                        target_title = matching[0]["title"]
                        reason = "chat_match"
                    elif len(matching) > 1:
                        reason = "skipped:ambiguous_chat_match"

                # Pass 3: Fallback matching for rows still unmatched via telegram_user_id
                if target_ws is None and not reason:
                    if user_id_str:
                        user_matching = ws_by_chat.get(user_id_str, [])
                        if len(user_matching) == 1:
                            target_ws = user_matching[0]["id"]
                            target_title = user_matching[0]["title"]
                            reason = "user_match"
                        elif len(user_matching) > 1:
                            reason = "skipped:ambiguous_user_match"

                if target_ws is None and not reason:
                    if not origin_chat and not user_id_str:
                        reason = "skipped:no_chat_or_user_id"
                    else:
                        reason = "skipped:no_matching_workspace"

                if target_ws is not None:
                    if current_ws == target_ws:
                        status = "already_matched"
                        summary["transactions"]["skipped"]["already_matched"] += 1
                    else:
                        status = "retagged" if apply else "would_retag"
                        summary["transactions"]["retagged"] += 1
                        touched_workspaces.add(target_ws)
                        if current_ws and current_ws in active_ws_ids:
                            touched_workspaces.add(current_ws)
                        if apply:
                            cursor.execute("UPDATE transactions SET workspace_id = ? WHERE id = ?", (target_ws, tx_id))
                else:
                    status = "skipped"
                    summary["transactions"]["skipped"].setdefault(reason, 0)
                    summary["transactions"]["skipped"][reason] += 1
                    if "ambiguous" in reason:
                        summary["transactions"]["skipped"]["ambiguous_matching_workspaces"] += 1
                    elif reason in ("skipped:origin_less", "skipped:no_chat_or_user_id"):
                        summary["transactions"]["skipped"]["origin_less"] += 1
                    elif "no_matching" in reason:
                        summary["transactions"]["skipped"]["no_matching_workspace"] += 1

                summary["transactions"]["details"].append({
                    "id": tx_id,
                    "status": status,
                    "from_workspace": current_ws,
                    "to_workspace": target_ws,
                    "workspace_title": target_title,
                    "reason": reason,
                })

        # 2. Inspect undo_log
        if "undo_log" in existing_tables:
            cursor.execute("PRAGMA table_info(undo_log)")
            undo_cols = {r[1] for r in cursor.fetchall()}
            has_undo_user = "user_id" in undo_cols

            if has_undo_user:
                cursor.execute("SELECT id, workspace_id, chat_id, user_id FROM undo_log ORDER BY id ASC")
            else:
                cursor.execute("SELECT id, workspace_id, chat_id, NULL as user_id FROM undo_log ORDER BY id ASC")
            undo_rows = cursor.fetchall()
            summary["undo_log"]["scanned"] = len(undo_rows)

            for u_rec in undo_rows:
                u_id = u_rec["id"]
                current_ws = str(u_rec["workspace_id"]).strip() if u_rec["workspace_id"] else ""
                origin_chat = str(u_rec["chat_id"]).strip() if u_rec["chat_id"] is not None else ""
                user_id_raw = u_rec["user_id"]
                user_id_str = str(user_id_raw).strip() if user_id_raw is not None else ""
                user_id_int = None
                if user_id_str:
                    try:
                        user_id_int = int(user_id_str)
                    except ValueError:
                        pass

                target_ws = None
                target_title = ""
                reason = None

                # Pass 1: Global owner rows ALWAYS go home to default workspace (Fix 8 step 1)
                if (owner_id and user_id_int == owner_id) or (origin_chat and origin_chat == str(owner_id)):
                    if default_ws_id and default_ws_id in active_ws_ids:
                        target_ws = default_ws_id
                        target_title = ws_id_to_title.get(default_ws_id, "Default Workspace")
                        reason = "owner_default"
                    else:
                        reason = "skipped:no_default_workspace"
                # Pass 2: chat_id exact-match against active workspaces
                elif origin_chat:
                    matching = ws_by_chat.get(origin_chat, [])
                    if len(matching) == 1:
                        target_ws = matching[0]["id"]
                        target_title = matching[0]["title"]
                        reason = "chat_match"
                    elif len(matching) > 1:
                        reason = "skipped:ambiguous_chat_match"

                # Pass 3: Fallback matching for rows still unmatched via user_id
                if target_ws is None and not reason:
                    if user_id_str:
                        user_matching = ws_by_chat.get(user_id_str, [])
                        if len(user_matching) == 1:
                            target_ws = user_matching[0]["id"]
                            target_title = user_matching[0]["title"]
                            reason = "user_match"
                        elif len(user_matching) > 1:
                            reason = "skipped:ambiguous_user_match"

                if target_ws is None and not reason:
                    if not origin_chat and not user_id_str:
                        reason = "skipped:no_chat_or_user_id"
                    else:
                        reason = "skipped:no_matching_workspace"

                if target_ws is not None:
                    if current_ws == target_ws:
                        status = "already_matched"
                        summary["undo_log"]["skipped"]["already_matched"] += 1
                    else:
                        status = "retagged" if apply else "would_retag"
                        summary["undo_log"]["retagged"] += 1
                        touched_workspaces.add(target_ws)
                        if current_ws and current_ws in active_ws_ids:
                            touched_workspaces.add(current_ws)
                        if apply:
                            cursor.execute("UPDATE undo_log SET workspace_id = ? WHERE id = ?", (target_ws, u_id))
                else:
                    status = "skipped"
                    summary["undo_log"]["skipped"].setdefault(reason, 0)
                    summary["undo_log"]["skipped"][reason] += 1
                    if "ambiguous" in reason:
                        summary["undo_log"]["skipped"]["ambiguous_matching_workspaces"] += 1
                    elif reason in ("skipped:origin_less", "skipped:no_chat_or_user_id"):
                        summary["undo_log"]["skipped"]["origin_less"] += 1
                    elif "no_matching" in reason:
                        summary["undo_log"]["skipped"]["no_matching_workspace"] += 1

                summary["undo_log"]["details"].append({
                    "id": u_id,
                    "status": status,
                    "from_workspace": current_ws,
                    "to_workspace": target_ws,
                    "workspace_title": target_title,
                    "reason": reason,
                })

        # 3. Inspect obsolete members (Data Note: do NOT auto-delete)
        summary["obsolete_members"] = {
            "scanned": 0,
            "flagged": 0,
            "details": []
        }
        if "workspace_members" in existing_tables and "workspaces" in existing_tables:
            import config
            owner_id = int(getattr(config, 'TELEGRAM_USER_ID', 0) or 0)
            cursor.execute("""
                SELECT wm.id, wm.workspace_id, wm.telegram_user_id, wm.role, wm.username, wm.display_name,
                       w.chat_id, w.title as workspace_title, w.chat_type
                FROM workspace_members wm
                JOIN workspaces w ON wm.workspace_id = w.id
                ORDER BY wm.id ASC
            """)
            wm_rows = cursor.fetchall()
            summary["obsolete_members"]["scanned"] = len(wm_rows)
            for m in wm_rows:
                m_uid = m["telegram_user_id"]
                w_chat = str(m["chat_id"]).strip() if m["chat_id"] is not None else ""
                # Under the one-person model, members rows placing non-owners inside another user's workspace are obsolete
                if owner_id and m_uid == owner_id:
                    continue
                if w_chat and str(m_uid) == w_chat:
                    continue
                summary["obsolete_members"]["flagged"] += 1
                summary["obsolete_members"]["details"].append({
                    "id": m["id"],
                    "workspace_id": m["workspace_id"],
                    "workspace_title": m["workspace_title"],
                    "telegram_user_id": m_uid,
                    "username": m["username"],
                    "display_name": m["display_name"],
                    "role": m["role"],
                    "chat_id": w_chat,
                    "reason": f"Non-owner user {m_uid} present in foreign workspace '{m['workspace_title']}' (chat_id={w_chat})"
                })

        if apply:
            # Fix 8 step 3: Post-repair deactivation of stale owner personal workspaces
            if owner_id and default_ws_id:
                try:
                    cursor.execute("""
                        UPDATE workspaces
                        SET is_active = 0
                        WHERE is_active = 1
                          AND id != ?
                          AND (chat_id = ? OR (title LIKE '%Personal%' AND chat_id = ?))
                    """, (default_ws_id, owner_id, owner_id))
                    deactivated = cursor.rowcount
                    summary["deactivated_stale_owner_workspaces"] = deactivated
                except Exception:
                    pass

            try:
                from services.balance_service import recalculate_in_connection
                for ws_id in touched_workspaces:
                    if ws_id in active_ws_ids:
                        recalculate_in_connection(conn, workspace_id=ws_id)
            except Exception:
                pass
            conn.commit()

        return summary
    finally:
        if close_conn:
            conn.close()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Repair misplaced workspace_id provenance in Payment Tracker.")
    parser.add_argument("--apply", action="store_true", help="Apply changes to the database (default is dry-run).")
    parser.add_argument("--db", type=str, default=None, help="Custom path to payment_tracker.db SQLite database.")
    args = parser.parse_args()

    mode_str = "APPLY (CHANGES WILL BE COMMITTED)" if args.apply else "DRY-RUN (NO CHANGES MADE)"
    print(f"\n==================================================")
    print(f" Payment Tracker - Workspace Provenance Repair")
    print(f" Mode: {mode_str}")
    print(f"==================================================\n")

    result = repair_provenance(conn_or_db_path=args.db, apply=args.apply)

    if not result.get("success"):
        print(f"Error: {result.get('error')}")
        sys.exit(1)

    for table in ["transactions", "undo_log"]:
        t_data = result[table]
        retag_label = "Re-tagged" if args.apply else "Candidates to re-tag"
        print(f"[{table.upper()}]")
        print(f"  * Total Scanned: {t_data['scanned']}")
        print(f"  * {retag_label}: {t_data['retagged']}")
        print(f"  * Skipped breakdown: {dict(t_data['skipped'])}")
        if t_data["details"]:
            print(f"  * Row Details (old workspace_id -> new workspace_id + reason):")
            for d in t_data["details"]:
                from_ws = d.get('from_workspace') or '<none>'
                to_ws = d.get('to_workspace') or '<none>'
                reason = d.get('reason') or 'unknown'
                print(f"      [#{d['id']}] {from_ws} -> {to_ws} + {reason}")
        print()

    if "obsolete_members" in result:
        m_data = result["obsolete_members"]
        print(f"[OBSOLETE MEMBERS (REVIEW ONLY - NOT DELETED)]")
        print(f"  * Total Scanned: {m_data['scanned']}")
        print(f"  * Foreign/Obsolete Members Flagged: {m_data['flagged']}")
        if m_data["details"]:
            print(f"  * Flagged Members for Owner Review:")
            for d in m_data["details"][:10]:
                print(f"      [User {d['telegram_user_id']}] role={d['role']} in '{d['workspace_title']}' (ws={d['workspace_id']})")
            if len(m_data["details"]) > 10:
                print(f"      ... and {len(m_data['details']) - 10} more.")
        print()

    if not args.apply:
        print("[*] Dry-run complete. Run with --apply to commit these repairs.\n")
    else:
        print("[OK] Repairs successfully applied and committed to database.\n")


if __name__ == "__main__":
    main()
