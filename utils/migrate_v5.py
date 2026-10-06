"""
Migration script for Payment Tracker v5 (Comprehensive Multi-Tenant Hardening):
1. Creates workspace_invites table for secure hashed invitations.
2. Creates audit_logs table for immutable tenant action trails.
3. Creates workspace_job_runs table for scheduler execution deduplication.
4. Creates dashboard_auth_codes and dashboard_sessions tables for persistent session state.
5. Adds status column to workspaces and workspace_members.
6. Adds workspace_id column to access_requests.
7. Migrates payee_categories, custom_menu_items, and monthly_reviews to composite workspace-scoped unique constraints.
8. Backfills any remaining unscoped rows in domain tables.
9. Upgrades schema_version to 5.
"""

import sys
import shutil
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "database.sqlite3"


def run_migration(dry_run: bool = False, db_path: Path = None):
    target_db = Path(db_path or DB_PATH).resolve()
    print(f"=== Starting Migration v5 on: {target_db} (Dry Run: {dry_run}) ===")

    if not target_db.exists():
        print(f"Error: Database file does not exist at {target_db}")
        return False

    # 1. Pre-migration backup if not dry run
    if not dry_run:
        backup_path = target_db.with_suffix(".sqlite3.backup-v5")
        shutil.copy2(target_db, backup_path)
        print(f"Pre-migration snapshot saved to: {backup_path}")

    conn = sqlite3.connect(str(target_db), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        now_utc = datetime.now(timezone.utc).isoformat()

        # Check default workspace id
        cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
        row = cursor.fetchone()
        default_ws_id = str(row['value']) if row and row['value'] else None
        if not default_ws_id:
            cursor.execute("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1")
            ws_row = cursor.fetchone()
            default_ws_id = str(ws_row['id']) if ws_row and ws_row['id'] else "default"

        print(f"Using default workspace ID for backfill: {default_ws_id}")

        # 2. Table: workspace_invites
        print("Checking/creating 'workspace_invites'...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspace_invites (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_by INTEGER NOT NULL,
                intended_role TEXT NOT NULL DEFAULT 'member' CHECK(intended_role IN ('admin', 'member', 'viewer')),
                max_uses INTEGER NOT NULL DEFAULT 1,
                uses_count INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL,
                revoked_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invites_ws ON workspace_invites(workspace_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invites_hash ON workspace_invites(token_hash)")

        # 3. Table: audit_logs
        print("Checking/creating 'audit_logs'...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                actor_user_id INTEGER NOT NULL,
                actor_role TEXT NOT NULL DEFAULT 'member',
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                request_id TEXT DEFAULT '',
                result TEXT NOT NULL DEFAULT 'success',
                details_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_ws_created ON audit_logs(workspace_id, created_at DESC)")

        # 4. Table: workspace_job_runs
        print("Checking/creating 'workspace_job_runs'...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspace_job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                job_name TEXT NOT NULL,
                scheduled_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                executed_at TEXT NOT NULL,
                UNIQUE(workspace_id, job_name, scheduled_date)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_ws_job ON workspace_job_runs(workspace_id, job_name, scheduled_date)")

        # 5. Tables: dashboard_auth_codes & dashboard_sessions
        print("Checking/creating dashboard session tables...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_auth_codes (
                code_hash TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                expires_at REAL NOT NULL,
                used_at REAL DEFAULT NULL,
                created_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_sessions (
                session_id_hash TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                csrf_token TEXT NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL DEFAULT NULL,
                created_at REAL NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_dash_sess_ws_user ON dashboard_sessions(workspace_id, user_id)")

        # 6. Columns: status on workspaces & workspace_members
        cursor.execute("PRAGMA table_info(workspaces)")
        ws_cols = [c['name'] for c in cursor.fetchall()]
        if "status" not in ws_cols:
            print("Adding 'status' column to 'workspaces'...")
            cursor.execute("ALTER TABLE workspaces ADD COLUMN status TEXT DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'archived', 'deleted'))")
            cursor.execute("UPDATE workspaces SET status = 'active' WHERE status IS NULL OR status = ''")

        cursor.execute("PRAGMA table_info(workspace_members)")
        wm_cols = [c['name'] for c in cursor.fetchall()]
        if "status" not in wm_cols:
            print("Adding 'status' column to 'workspace_members'...")
            cursor.execute("ALTER TABLE workspace_members ADD COLUMN status TEXT DEFAULT 'active' CHECK(status IN ('pending', 'active', 'suspended', 'removed'))")
            cursor.execute("UPDATE workspace_members SET status = 'active' WHERE status IS NULL OR status = ''")

        # 7. Column: workspace_id on access_requests
        cursor.execute("PRAGMA table_info(access_requests)")
        ar_cols = [c['name'] for c in cursor.fetchall()]
        if "workspace_id" not in ar_cols:
            print("Adding 'workspace_id' column to 'access_requests'...")
            cursor.execute("ALTER TABLE access_requests ADD COLUMN workspace_id TEXT DEFAULT NULL")

        # 8. Re-table payee_categories for composite PK (workspace_id, payee_name)
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='payee_categories'")
        pc_sql_row = cursor.fetchone()
        if pc_sql_row and "PRIMARY KEY (workspace_id, payee_name)" not in pc_sql_row['sql'] and "PRIMARY KEY(workspace_id, payee_name)" not in pc_sql_row['sql']:
            print("Rebuilding 'payee_categories' with composite PK (workspace_id, payee_name)...")
            cursor.execute("""
                CREATE TABLE payee_categories_v5 (
                    workspace_id TEXT NOT NULL,
                    payee_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY (workspace_id, payee_name)
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO payee_categories_v5 (workspace_id, payee_name, category, updated_at)
                SELECT COALESCE(workspace_id, ?), payee_name, category, updated_at FROM payee_categories
            """, (default_ws_id,))
            cursor.execute("DROP TABLE payee_categories")
            cursor.execute("ALTER TABLE payee_categories_v5 RENAME TO payee_categories")

        # 9. Re-table custom_menu_items for composite UNIQUE (workspace_id, name)
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='custom_menu_items'")
        cmi_sql_row = cursor.fetchone()
        if cmi_sql_row and "name TEXT UNIQUE" in cmi_sql_row['sql']:
            print("Rebuilding 'custom_menu_items' with composite UNIQUE (workspace_id, name)...")
            cursor.execute("""
                CREATE TABLE custom_menu_items_v5 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    category TEXT DEFAULT 'Snacks & Tea',
                    is_veg INTEGER DEFAULT 1,
                    created_at TEXT,
                    UNIQUE(workspace_id, name)
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO custom_menu_items_v5 (id, workspace_id, name, price, category, is_veg, created_at)
                SELECT id, COALESCE(workspace_id, ?), name, price, category, is_veg, created_at FROM custom_menu_items
            """, (default_ws_id,))
            cursor.execute("DROP TABLE custom_menu_items")
            cursor.execute("ALTER TABLE custom_menu_items_v5 RENAME TO custom_menu_items")

        # 10. Re-table monthly_reviews for composite UNIQUE (workspace_id, year, month)
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='monthly_reviews'")
        mr_sql_row = cursor.fetchone()
        if mr_sql_row and "UNIQUE(year, month)" in mr_sql_row['sql']:
            print("Rebuilding 'monthly_reviews' with composite UNIQUE (workspace_id, year, month)...")
            cursor.execute("""
                CREATE TABLE monthly_reviews_v5 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    total_income REAL NOT NULL,
                    total_expense REAL NOT NULL,
                    net_savings REAL NOT NULL,
                    savings_rate_pct REAL NOT NULL,
                    top_category TEXT,
                    top_category_amount REAL,
                    top_payee TEXT,
                    top_payee_amount REAL,
                    max_transaction_id INTEGER,
                    max_transaction_amount REAL,
                    budget_allocated REAL,
                    budget_spent_pct REAL,
                    is_closed INTEGER DEFAULT 1,
                    reviewed_at TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, year, month)
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO monthly_reviews_v5 (
                    id, workspace_id, year, month, total_income, total_expense, net_savings,
                    savings_rate_pct, top_category, top_category_amount, top_payee, top_payee_amount,
                    max_transaction_id, max_transaction_amount, budget_allocated, budget_spent_pct,
                    is_closed, reviewed_at, notes, created_at
                )
                SELECT 
                    id, COALESCE(workspace_id, ?), year, month, total_income, total_expense, net_savings,
                    savings_rate_pct, top_category, top_category_amount, top_payee, top_payee_amount,
                    max_transaction_id, max_transaction_amount, budget_allocated, budget_spent_pct,
                    is_closed, reviewed_at, notes, created_at
                FROM monthly_reviews
            """, (default_ws_id,))
            cursor.execute("DROP TABLE monthly_reviews")
            cursor.execute("ALTER TABLE monthly_reviews_v5 RENAME TO monthly_reviews")

        # 11. Backfill any null workspace_id in undo_log
        print("Backfilling any null workspace_id in 'undo_log'...")
        cursor.execute("UPDATE undo_log SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = ''", (default_ws_id,))

        # 12. Update schema version
        cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('schema_version', '5', ?)", (now_utc,))

        # Validation Check
        print("\n--- Running Validation Checks ---")
        unscoped_counts = {}
        for t_name in ['transactions', 'custom_menu_items', 'payee_categories', 'recurring_payments', 'monthly_reviews', 'undo_log']:
            cursor.execute(f"SELECT COUNT(*) FROM {t_name} WHERE workspace_id IS NULL OR workspace_id = ''")
            cnt = cursor.fetchone()[0]
            unscoped_counts[t_name] = cnt
            print(f"  {t_name}: {cnt} unscoped rows")

        assert all(c == 0 for c in unscoped_counts.values()), f"Validation failed! Found unscoped rows: {unscoped_counts}"
        print("All domain tables verified 100% scoped by workspace_id.")

        if dry_run:
            conn.rollback()
            print("\nDry run completed successfully. (All changes rolled back)")
        else:
            conn.commit()
            print("\nMigration v5 committed successfully! Schema upgraded to version 5.")
        return True

    except Exception as e:
        conn.rollback()
        print(f"\nMigration v5 failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Payment Tracker Schema Migration v5")
    parser.add_argument("--dry-run", action="store_true", help="Perform checks and migrations in transaction then rollback")
    parser.add_argument("--db", type=str, default=None, help="Path to database file")
    args = parser.parse_args()
    success = run_migration(dry_run=args.dry_run, db_path=args.db)
    sys.exit(0 if success else 1)
