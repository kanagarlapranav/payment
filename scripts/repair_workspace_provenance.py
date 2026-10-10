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

        # Cache active workspaces by chat_id string
        cursor.execute("SELECT id, chat_id, title FROM workspaces WHERE is_active = 1")
        ws_by_chat: dict[str, list[dict[str, Any]]] = {}
        for w in cursor.fetchall():
            c_val = str(w["chat_id"]).strip() if w["chat_id"] is not None else ""
            if c_val:
                ws_by_chat.setdefault(c_val, []).append(dict(w))

        summary: dict[str, Any] = {
            "success": True,
            "apply": apply,
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
            cursor.execute("SELECT id, workspace_id, telegram_chat_id, person_name, amount, transaction_date FROM transactions ORDER BY id ASC")
            tx_rows = cursor.fetchall()
            summary["transactions"]["scanned"] = len(tx_rows)

            for tx in tx_rows:
                tx_id = tx["id"]
                current_ws = str(tx["workspace_id"]).strip() if tx["workspace_id"] else ""
                origin_chat = str(tx["telegram_chat_id"]).strip() if tx["telegram_chat_id"] is not None else ""

                if not origin_chat:
                    summary["transactions"]["skipped"]["origin_less"] += 1
                    continue

                matching = ws_by_chat.get(origin_chat, [])
                if len(matching) == 0:
                    summary["transactions"]["skipped"]["no_matching_workspace"] += 1
                    summary["transactions"]["details"].append({
                        "id": tx_id,
                        "status": "skipped",
                        "reason": f"no active workspace found for chat_id={origin_chat}",
                        "current_workspace": current_ws,
                    })
                elif len(matching) > 1:
                    summary["transactions"]["skipped"]["ambiguous_matching_workspaces"] += 1
                    summary["transactions"]["details"].append({
                        "id": tx_id,
                        "status": "skipped",
                        "reason": f"ambiguous: {len(matching)} active workspaces match chat_id={origin_chat}",
                        "current_workspace": current_ws,
                    })
                else:
                    target_ws = matching[0]["id"]
                    if current_ws == target_ws:
                        summary["transactions"]["skipped"]["already_matched"] += 1
                    else:
                        summary["transactions"]["retagged"] += 1
                        summary["transactions"]["details"].append({
                            "id": tx_id,
                            "status": "retagged" if apply else "would_retag",
                            "chat_id": origin_chat,
                            "from_workspace": current_ws,
                            "to_workspace": target_ws,
                            "workspace_title": matching[0]["title"],
                        })
                        if apply:
                            cursor.execute("UPDATE transactions SET workspace_id = ? WHERE id = ?", (target_ws, tx_id))

        # 2. Inspect undo_log
        if "undo_log" in existing_tables:
            cursor.execute("SELECT id, workspace_id, chat_id, action, uid FROM undo_log ORDER BY id ASC")
            undo_rows = cursor.fetchall()
            summary["undo_log"]["scanned"] = len(undo_rows)

            for u_rec in undo_rows:
                u_id = u_rec["id"]
                current_ws = str(u_rec["workspace_id"]).strip() if u_rec["workspace_id"] else ""
                u_chat = str(u_rec["chat_id"]).strip() if u_rec["chat_id"] is not None else ""

                if not u_chat:
                    summary["undo_log"]["skipped"]["origin_less"] += 1
                    continue

                matching = ws_by_chat.get(u_chat, [])
                if len(matching) == 0:
                    summary["undo_log"]["skipped"]["no_matching_workspace"] += 1
                    summary["undo_log"]["details"].append({
                        "id": u_id,
                        "status": "skipped",
                        "reason": f"no active workspace found for chat_id={u_chat}",
                        "current_workspace": current_ws,
                    })
                elif len(matching) > 1:
                    summary["undo_log"]["skipped"]["ambiguous_matching_workspaces"] += 1
                    summary["undo_log"]["details"].append({
                        "id": u_id,
                        "status": "skipped",
                        "reason": f"ambiguous: {len(matching)} active workspaces match chat_id={u_chat}",
                        "current_workspace": current_ws,
                    })
                else:
                    target_ws = matching[0]["id"]
                    if current_ws == target_ws:
                        summary["undo_log"]["skipped"]["already_matched"] += 1
                    else:
                        summary["undo_log"]["retagged"] += 1
                        summary["undo_log"]["details"].append({
                            "id": u_id,
                            "status": "retagged" if apply else "would_retag",
                            "chat_id": u_chat,
                            "from_workspace": current_ws,
                            "to_workspace": target_ws,
                            "workspace_title": matching[0]["title"],
                        })
                        if apply:
                            cursor.execute("UPDATE undo_log SET workspace_id = ? WHERE id = ?", (target_ws, u_id))

        if apply:
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
        print(f"  * Skipped:")
        print(f"      - Already matched: {t_data['skipped']['already_matched']}")
        print(f"      - Origin-less (no chat id): {t_data['skipped']['origin_less']}")
        print(f"      - No active workspace match: {t_data['skipped']['no_matching_workspace']}")
        print(f"      - Ambiguous match (>1 workspace): {t_data['skipped']['ambiguous_matching_workspaces']}")
        if t_data["details"]:
            print(f"  * Sample items:")
            for d in t_data["details"][:10]:
                if d.get("status") in ("retagged", "would_retag"):
                    print(f"      [#{d['id']}] chat={d['chat_id']} : {d['from_workspace']} -> {d['to_workspace']} ({d['workspace_title']})")
                else:
                    print(f"      [#{d['id']}] {d.get('reason')} (current={d.get('current_workspace')})")
            if len(t_data["details"]) > 10:
                print(f"      ... and {len(t_data['details']) - 10} more.")
        print()

    if not args.apply:
        print("[*] Dry-run complete. Run with --apply to commit these repairs.\n")
    else:
        print("[OK] Repairs successfully applied and committed to database.\n")


if __name__ == "__main__":
    main()
