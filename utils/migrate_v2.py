"""
Migration script for Payment Tracker v2:
1. Adds `uid` and `deleted_at` columns to `transactions` if missing.
2. Populates `uid` with a 32-character hexadecimal UUID for all rows.
3. Creates unique index on `uid` and partial unique index on live `reference_number`.
4. Adds `backup_revision` setting if missing.
5. Restores categories from `data/recovered_16_transactions.json` for the 13 matching transactions.
6. Archives `data/recovered_16_transactions.json` to `data/archive/recovered_16_transactions.json`.
"""
import os
import json
import uuid
import sqlite3
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "database.sqlite3"
RECOVERED_PATH = DATA_DIR / "recovered_16_transactions.json"
ARCHIVE_DIR = DATA_DIR / "archive"

def run_migration():
    print(f"Opening database at {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Check existing columns
    cursor.execute("PRAGMA table_info(transactions)")
    columns = [row["name"] for row in cursor.fetchall()]
    print(f"Current columns in transactions: {columns}")

    if "uid" not in columns:
        print("Adding 'uid' column...")
        cursor.execute("ALTER TABLE transactions ADD COLUMN uid TEXT")
    
    if "deleted_at" not in columns:
        print("Adding 'deleted_at' column...")
        cursor.execute("ALTER TABLE transactions ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL")

    # 2. Assign permanent uids to any row without one
    cursor.execute("SELECT id, uid FROM transactions WHERE uid IS NULL OR uid = ''")
    rows_needing_uid = cursor.fetchall()
    print(f"Rows needing permanent UID: {len(rows_needing_uid)}")
    for r in rows_needing_uid:
        new_uid = uuid.uuid4().hex
        cursor.execute("UPDATE transactions SET uid = ? WHERE id = ?", (new_uid, r["id"]))

    # 3. Create indices
    print("Creating indexes...")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_uid ON transactions(uid)")
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_ref_live ON transactions(reference_number)
        WHERE reference_number IS NOT NULL AND reference_number != '' AND deleted_at IS NULL
    """)

    # 4. Add revision setting
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('backup_revision', '1')")

    # 5. Restore categories from recovered_16_transactions.json
    if RECOVERED_PATH.exists():
        print(f"Found {RECOVERED_PATH}. Inspecting categories to restore...")
        with open(RECOVERED_PATH, "r", encoding="utf-8") as f:
            rec_data = json.load(f)
        rec_txs = rec_data.get("transactions", [])

        # Fetch current live transactions
        cursor.execute("SELECT * FROM transactions")
        live_txs = [dict(row) for row in cursor.fetchall()]

        updated_categories = 0
        for l in live_txs:
            for r in rec_txs:
                # Match on reference number if present, or amount + person_name
                ref_match = (
                    l.get("reference_number") and
                    r.get("reference_number") and
                    l["reference_number"] == r["reference_number"]
                )
                person_match = (
                    abs(float(l.get("amount", 0)) - float(r.get("amount", 0))) < 0.01 and
                    (l.get("person_name") or "").strip().lower() == (r.get("person_name") or "").strip().lower()
                )
                if ref_match or person_match:
                    rec_cat = r.get("category")
                    if rec_cat and rec_cat != "General" and l.get("category") == "General":
                        cursor.execute(
                            "UPDATE transactions SET category = ? WHERE id = ?",
                            (rec_cat, l["id"])
                        )
                        print(f"Updated Tx #{l['id']} ({l['person_name']}, Rs {l['amount']}): Category -> '{rec_cat}'")
                        updated_categories += 1
                        break

        print(f"Restored categories for {updated_categories} transactions.")

    conn.commit()

    # Verify counts
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL")
    live_count = cursor.fetchone()[0]
    cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
    curr_bal = cursor.fetchone()[0]
    print(f"Live transactions: {live_count}, Current balance setting: Rs {curr_bal}")
    conn.close()

    # 6. Archive recovered_16_transactions.json
    if RECOVERED_PATH.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dest = ARCHIVE_DIR / "recovered_16_transactions.json"
        shutil.move(str(RECOVERED_PATH), str(dest))
        print(f"Archived {RECOVERED_PATH} -> {dest}")

    print("Migration v2 completed successfully.")

if __name__ == "__main__":
    run_migration()
