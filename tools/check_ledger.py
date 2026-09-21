#!/usr/bin/env python3
"""
CLI tool for validating ledger integrity invariants.
Exits 0 on success (clean ledger), exits 1 if any invariant error is found.
Usage:
    python tools/check_ledger.py [optional_path_to_db]
"""

import os
import sys
from pathlib import Path

# Ensure root package directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import DB_PATH
from services.balance_service import validate_ledger_invariants


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        target_db = Path(sys.argv[1].strip()).resolve()
    elif os.getenv("DATABASE_PATH"):
        target_db = Path(os.getenv("DATABASE_PATH").strip()).resolve()
    elif os.getenv("DATA_DIR"):
        target_db = (Path(os.getenv("DATA_DIR").strip()) / "database.sqlite3").resolve()
    else:
        target_db = DB_PATH.resolve()

    print(f"[*] Validating ledger invariants on: {target_db}")

    if not target_db.exists():
        print(f"[!] Error: Database file does not exist at '{target_db}'", file=sys.stderr)
        sys.exit(1)

    errors = validate_ledger_invariants(db_path=target_db)

    if errors:
        print(f"\n[FAIL] Found {len(errors)} ledger invariant error(s):", file=sys.stderr)
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}", file=sys.stderr)
        print("\nExiting with status 1.", file=sys.stderr)
        sys.exit(1)
    else:
        print("\n[OK] All ledger invariants validated successfully (0 errors found).")
        sys.exit(0)


if __name__ == "__main__":
    main()
