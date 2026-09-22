#!/usr/bin/env python3
"""
CLI tool to verify Test Isolation & Zero-Data-Loss guarantee.
Ensures running the test suite NEVER touches, mutates, creates, or pollutes production data.

Monitored Production Files:
  - data/database.sqlite3
  - data/database.sqlite3-wal
  - data/database.sqlite3-shm
  - data/backup_transactions.json

Usage: python tools/verify_test_isolation.py
Exit codes:
  0: Isolation verified (production data untouched)
  1: Isolation failure (production data altered by tests)
"""

import sys
import os
import hashlib
import shutil
import tempfile
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROD_DATA_DIR = (PROJECT_ROOT / "data").resolve()

PROD_FILES = [
    PROD_DATA_DIR / "database.sqlite3",
    PROD_DATA_DIR / "database.sqlite3-wal",
    PROD_DATA_DIR / "database.sqlite3-shm",
    PROD_DATA_DIR / "backup_transactions.json",
]


def get_file_metadata(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "sha256": "", "size": 0, "mtime": 0.0}
    stat = path.stat()
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "exists": True,
        "sha256": sha256,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def main():
    if not PROD_DATA_DIR.exists():
        print(f"[ERROR] Production data directory not found at: {PROD_DATA_DIR}")
        sys.exit(1)

    print("[*] Recording pre-test production file metadata...")
    before_meta = {}
    for pf in PROD_FILES:
        rel_name = pf.relative_to(PROJECT_ROOT)
        meta = get_file_metadata(pf)
        before_meta[pf] = meta
        if meta["exists"]:
            print(f"    - {rel_name}: SHA={meta['sha256'][:16]}... Size={meta['size']}B mtime={meta['mtime']}")
        else:
            print(f"    - {rel_name}: (File does not exist yet)")

    # Create an explicit unique OS temp directory for isolation test run
    temp_dir = tempfile.mkdtemp(prefix="iso_verify_")
    temp_data_dir = Path(temp_dir) / "data"
    temp_data_dir.mkdir(parents=True, exist_ok=True)
    temp_db_path = temp_data_dir / "isolated_test_db.sqlite3"
    temp_backup_path = temp_data_dir / "isolated_test_backup.json"
    temp_log_dir = temp_data_dir / "logs"

    print(f"\n[*] Isolated test database path: {temp_db_path}")

    # Set env vars strictly pointing to temporary directory
    test_env = os.environ.copy()
    test_env["PAYMENT_TRACKER_ENV"] = "test"
    test_env["DATA_DIR"] = str(temp_data_dir)
    test_env["DATABASE_PATH"] = str(temp_db_path)
    test_env["BACKUP_JSON_PATH"] = str(temp_backup_path)
    test_env["LOG_DIR"] = str(temp_log_dir)
    test_env["TELEGRAM_USER_ID"] = "123456789"

    python_exe = sys.executable
    cmd = [python_exe, "-m", "pytest", "-q"]

    try:
        print("\n[*] Running pytest suite inside isolated environment...")
        res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=test_env)

        print("\n[*] Inspecting post-test production file metadata...")
        failed = False
        for pf in PROD_FILES:
            rel_name = pf.relative_to(PROJECT_ROOT)
            b_meta = before_meta[pf]
            a_meta = get_file_metadata(pf)

            if b_meta["exists"] != a_meta["exists"]:
                print(f"[FAIL] CRITICAL: {rel_name} existence state changed! (Before: {b_meta['exists']}, After: {a_meta['exists']})")
                failed = True
            elif b_meta["exists"]:
                if b_meta["sha256"] != a_meta["sha256"]:
                    print(f"[FAIL] CRITICAL: {rel_name} content SHA-256 changed!")
                    print(f"       Before: {b_meta['sha256']}")
                    print(f"       After:  {a_meta['sha256']}")
                    failed = True
                elif b_meta["size"] != a_meta["size"]:
                    print(f"[FAIL] CRITICAL: {rel_name} size changed from {b_meta['size']} to {a_meta['size']} bytes!")
                    failed = True
                elif b_meta["mtime"] != a_meta["mtime"]:
                    print(f"[FAIL] CRITICAL: {rel_name} modification time changed!")
                    failed = True
                else:
                    print(f"[OK] {rel_name} is 100% byte-for-byte untouched.")
            else:
                print(f"[OK] {rel_name} remained absent (not created).")

        if res.returncode != 0:
            print(f"[FAIL] Pytest exited with non-zero exit code: {res.returncode}")
            failed = True
        else:
            print("[OK] Pytest suite executed successfully.")

        if failed:
            print("\n[FAILURE] Test isolation verification FAILED!")
            sys.exit(1)

        print("\n[SUCCESS] Test isolation verified: Zero production data touched or modified!")
        sys.exit(0)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
