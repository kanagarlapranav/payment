#!/usr/bin/env python3
"""
CLI tool to verify Format v2 backup integrity, canonical SHA-256 checksum, and transaction invariants.
Usage: python tools/verify_backup.py [path/to/backup.json]
Exit codes:
  0: Valid backup
  1: Invalid backup or verification error
"""

import sys
import json
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import DATA_DIR
from services.backup_service import verify_backup_payload, compute_canonical_checksum

def main():
    target_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR / "backup_transactions.json"
    print(f"[*] Verifying backup file: {target_path}")

    if not target_path.exists():
        print(f"[FAIL] Backup file not found at: {target_path}")
        sys.exit(1)

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[FAIL] Invalid JSON syntax: {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        print("[FAIL] Backup root is not a JSON object.")
        sys.exit(1)

    version = data.get("version", 1)
    rev = data.get("revision", "N/A")
    db_id = data.get("database_id", "N/A")
    txs = data.get("transactions", [])
    menu_items = data.get("custom_menu_items", [])
    budgets = data.get("budgets", [])
    balance = data.get("balance", "N/A")
    live_count = data.get("live_count", sum(1 for t in txs if not t.get("deleted_at")))
    tombstones = len(txs) - live_count

    valid, err_msg = verify_backup_payload(data)
    if not valid:
        print(f"[FAIL] Verification failed: {err_msg}")
        sys.exit(1)

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    print(f"[OK] Format Version: {version}")
    print(f"[OK] Revision: {rev}")
    print(f"[OK] Database ID: {db_id}")
    if version >= 2:
        print(f"[OK] Checksum: VALID (sha256:{data.get('checksum')})")
    print(f"[OK] Total records: {len(txs)} (Live: {live_count}, Tombstones: {tombstones})")
    print(f"[OK] Custom menu items: {len(menu_items)}")
    print(f"[OK] Budgets: {len(budgets)}")
    print(f"[OK] Recorded balance: Rs. {balance}")
    print("Backup verification SUCCESSFUL.")
    sys.exit(0)

if __name__ == "__main__":
    main()
