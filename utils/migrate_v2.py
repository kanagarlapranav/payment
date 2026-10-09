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

def run_migration(db_path: Path = DB_PATH, recovered_path: Path = RECOVERED_PATH):
    print(f"Opening database at {db_path}...")
    if db_path.exists():
        try:
            chk_conn = sqlite3.connect(str(db_path), timeout=30.0)
            chk_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            chk_conn.close()
        except Exception as e:
            print(f"Warning: WAL checkpoint before backup returned: {e}")
        backup_file = db_path.with_suffix(".sqlite3.backup-v2")
        shutil.copy2(db_path, backup_file)
        print(f"Pre-migration backup created: {backup_file}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
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

        # 4. Add revision setting (guard against missing settings table)
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('backup_revision', '1')")

        # 5. Restore categories from recovered_16_transactions.json
        if recovered_path.exists():
            print(f"Found {recovered_path}. Inspecting categories to restore...")
            with open(recovered_path, "r", encoding="utf-8") as f:
                rec_data = json.load(f)
            rec_txs = rec_data.get("transactions", [])

            # Fetch current live transactions
            cursor.execute("SELECT * FROM transactions")
            live_txs = [dict(row) for row in cursor.fetchall()]

            updated_categories = 0
            for l in live_txs:
                for r in rec_txs:
                    # Match strictly on exact uid or exact non-empty reference number
                    uid_match = (
                        l.get("uid") and
                        r.get("uid") and
                        str(l["uid"]).strip() != "" and
                        str(l["uid"]).strip() == str(r["uid"]).strip()
                    )
                    ref_match = (
                        l.get("reference_number") and
                        r.get("reference_number") and
                        str(l["reference_number"]).strip() != "" and
                        str(l["reference_number"]).strip() == str(r["reference_number"]).strip()
                    )
                    if uid_match or ref_match:
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
        curr_bal = "N/A"
        try:
            cursor.execute("SELECT value FROM settings WHERE key = 'current_balance'")
            row = cursor.fetchone()
            if row:
                curr_bal = row[0]
        except sqlite3.OperationalError:
            pass
        print(f"Live transactions: {live_count}, Current balance setting: Rs {curr_bal}")
    except Exception:
        conn.rollback()
        print("Migration v2 failed, rolled back.")
        raise
    finally:
        conn.close()

    # 6. Archive recovered_16_transactions.json
    if recovered_path.exists() and recovered_path == RECOVERED_PATH:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dest = ARCHIVE_DIR / "recovered_16_transactions.json"
        shutil.move(str(recovered_path), str(dest))
        print(f"Archived {recovered_path} -> {dest}")

    print("Migration v2 completed successfully.")

if __name__ == "__main__":
    run_migration()
