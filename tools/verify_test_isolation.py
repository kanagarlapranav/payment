#!/usr/bin/env python3
"""
CLI tool to verify Test Isolation & Zero-Data-Loss guarantee for primary database and backup files.
Ensures running the test suite NEVER touches, mutates, creates, or pollutes primary production database and backup files.

Monitored Production Files:
  - data/database.sqlite3
  - data/database.sqlite3-wal
  - data/database.sqlite3-shm
  - data/backup_transactions.json

Usage: python tools/verify_test_isolation.py
Exit codes:
  0: Isolation verified (production primary DB & backup files untouched)
  1: Isolation failure (production primary DB & backup files altered by tests)
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


def get_tree_metadata(root: Path) -> dict:
    meta = {}
    if not root.exists():
        return meta
    for p in root.rglob("*"):
        if p.is_file():
            # Allow pycache or local temp files if any
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(PROJECT_ROOT)
            stat = p.stat()
            sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
            meta[str(rel)] = {
                "sha256": sha256,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
    return meta


def main():
    if not PROD_DATA_DIR.exists():
        print(f"[ERROR] Production data directory not found at: {PROD_DATA_DIR}")
        sys.exit(1)

    print("[*] Recording pre-test production data tree metadata...")
    before_tree = get_tree_metadata(PROD_DATA_DIR)
    for rel_path, meta in before_tree.items():
        print(f"    - {rel_path}: SHA={meta['sha256'][:16]}... Size={meta['size']}B")

    # Create an explicit unique OS temp directory for isolation test run
    temp_dir = tempfile.mkdtemp(prefix="iso_verify_")
    temp_data_dir = Path(temp_dir) / "data"
    temp_data_dir.mkdir(parents=True, exist_ok=True)
    temp_db_path = temp_data_dir / "isolated_test_db.sqlite3"
    temp_backup_path = temp_data_dir / "isolated_test_backup.json"
    temp_log_dir = temp_data_dir / "logs"

    print(f"\n[*] Isolated test database path: {temp_db_path}")

    # Set env vars strictly pointing to temporary directory and scrub secrets
    test_env = os.environ.copy()
    test_env["PAYMENT_TRACKER_ENV"] = "test"
    test_env["DATA_DIR"] = str(temp_data_dir)
    test_env["DATABASE_PATH"] = str(temp_db_path)
    test_env["BACKUP_JSON_PATH"] = str(temp_backup_path)
    test_env["LOG_DIR"] = str(temp_log_dir)
    test_env["TELEGRAM_USER_ID"] = "123456789"

    # Scrub external API keys/tokens to prevent accidental network hits
    for secret_var in [
        "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "GEMINI_API_KEYS",
        "GDRIVE_SERVICE_ACCOUNT_JSON", "GDRIVE_BACKUP_FOLDER_ID", "DASHBOARD_TOKEN"
    ]:
        test_env.pop(secret_var, None)

    python_exe = sys.executable
    cmd = [python_exe, "-m", "pytest", "-q"]

    try:
        print("\n[*] Running pytest suite inside isolated environment...")
        res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=test_env)

        print("\n[*] Inspecting post-test production data tree metadata...")
        after_tree = get_tree_metadata(PROD_DATA_DIR)
        failed = False

        # Check for modified or deleted files
        for rel_path, b_meta in before_tree.items():
            if rel_path not in after_tree:
                print(f"[FAIL] CRITICAL: Production file deleted: {rel_path}")
                failed = True
            else:
                a_meta = after_tree[rel_path]
                if b_meta["sha256"] != a_meta["sha256"]:
                    print(f"[FAIL] CRITICAL: {rel_path} content SHA-256 changed!")
                    failed = True
                elif b_meta["size"] != a_meta["size"]:
                    print(f"[FAIL] CRITICAL: {rel_path} size changed!")
                    failed = True
                else:
                    print(f"[OK] {rel_path} is 100% byte-for-byte untouched.")

        # Check for newly created files under data/
        for rel_path in after_tree:
            if rel_path not in before_tree:
                print(f"[FAIL] CRITICAL: New file created in production data/: {rel_path}")
                failed = True

        if res.returncode != 0:
            print(f"[FAIL] Pytest exited with non-zero exit code: {res.returncode}")
            failed = True
        else:
            print("[OK] Pytest suite executed successfully.")

        if failed:
            print("\n[FAILURE] Test isolation verification FAILED!")
            sys.exit(1)

        print("\n[SUCCESS] Test isolation verified: Zero production data touched, created, or modified!")
        sys.exit(0)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
