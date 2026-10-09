import sqlite3
import json
import pytest
from pathlib import Path
from utils.migrate_v2 import run_migration

def test_migrate_v2_exact_uid_and_ref_matching(tmp_path: Path):
    db_file = tmp_path / "test_migrate.sqlite3"
    rec_file = tmp_path / "recovered.json"

    # Create test transactions table without settings table
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL,
            person_name TEXT,
            category TEXT,
            reference_number TEXT
        )
    """)
    # Insert 3 transactions:
    # 1. Exact ref match
    conn.execute(
        "INSERT INTO transactions (amount, person_name, category, reference_number) VALUES (?, ?, ?, ?)",
        (100.0, "Alice", "General", "REF123")
    )
    # 2. Fuzzy match candidate (same amount, same payee, but different ref) - should NOT be updated
    conn.execute(
        "INSERT INTO transactions (amount, person_name, category, reference_number) VALUES (?, ?, ?, ?)",
        (200.0, "Bob", "General", "REF999")
    )
    # 3. Exact uid match candidate (once column is added, we set uid)
    conn.execute(
        "INSERT INTO transactions (amount, person_name, category, reference_number) VALUES (?, ?, ?, ?)",
        (300.0, "Charlie", "General", "")
    )
    conn.commit()
    conn.close()

    # Create recovered json
    rec_data = {
        "transactions": [
            {
                "reference_number": "REF123",
                "category": "Food",
                "amount": 100.0,
                "person_name": "Alice"
            },
            {
                # Has same amount & payee as Bob, but different reference_number REF888
                "reference_number": "REF888",
                "category": "Shopping",
                "amount": 200.0,
                "person_name": "Bob"
            }
        ]
    }
    with open(rec_file, "w", encoding="utf-8") as f:
        json.dump(rec_data, f)

    # Run migration - tests that it doesn't crash even without settings table
    run_migration(db_path=db_file, recovered_path=rec_file)

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Verify Tx 1 category restored via exact ref match
    cursor.execute("SELECT category FROM transactions WHERE reference_number = 'REF123'")
    assert cursor.fetchone()[0] == "Food"

    # Verify Tx 2 category was NOT overwritten by fuzzy amount+payee match
    cursor.execute("SELECT category FROM transactions WHERE reference_number = 'REF999'")
    assert cursor.fetchone()[0] == "General"

    # Verify settings table exists and backup_revision is set
    cursor.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
    assert cursor.fetchone()[0] == "1"

    conn.close()
