import sqlite3
import threading
import uuid
from contextlib import contextmanager
from config import DB_PATH, logger

# Global re-entrant lock for multi-step SQLite mutations
LEDGER_LOCK = threading.RLock()

def _configure_connection(conn: sqlite3.Connection):
    """Applies high-reliability PRAGMAs to newly opened SQLite connection."""
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

@contextmanager
def get_db_connection(db_path=None):
    """
    Returns a fresh connection to the SQLite database with automatic commit and rollback.
    Configured with WAL mode, 30s busy timeout, NORMAL sync, and foreign keys enabled.
    Connections are never shared across threads.
    """
    target = db_path or DB_PATH
    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def setup_database():
    """Creates tables if they don't exist."""
    with LEDGER_LOCK:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Transactions table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        transaction_type TEXT NOT NULL,
                        amount REAL NOT NULL,
                        person_name TEXT,
                        sender_name TEXT,
                        recipient_name TEXT,
                        upi_id TEXT,
                        phone_number TEXT,
                        transaction_date DATE,
                        transaction_time TEXT,
                        reference_number TEXT,
                        transaction_id TEXT,
                        payment_app TEXT,
                        bank_name TEXT,
                        bank_account TEXT,
                        payment_status TEXT,
                        category TEXT DEFAULT 'General',
                        balance_before REAL NOT NULL,
                        balance_after REAL NOT NULL,
                        ocr_text TEXT,
                        original_image_path TEXT,
                        telegram_message_id TEXT,
                        telegram_chat_id TEXT,
                        uid TEXT UNIQUE,
                        workspace_id TEXT,
                        occurred_at TEXT,
                        deleted_at TEXT DEFAULT NULL,
                        created_at TEXT,
                        updated_at TEXT
                    )
                ''')
                
                # Migration checks: Ensure category, uid, workspace_id, deleted_at, occurred_at columns exist
                cursor.execute("PRAGMA table_info(transactions)")
                columns = [row[1] for row in cursor.fetchall()]
                if "category" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN category TEXT DEFAULT 'General'")
                if "uid" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN uid TEXT")
                cursor.execute("UPDATE transactions SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL OR uid = ''")
                if "workspace_id" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN workspace_id TEXT")
                if "deleted_at" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN deleted_at TEXT DEFAULT NULL")
                if "occurred_at" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN occurred_at TEXT")
                if "telegram_user_id" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN telegram_user_id INTEGER")

                # Multi-Tenant Workspaces Table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id TEXT PRIMARY KEY,
                        chat_id INTEGER UNIQUE NOT NULL,
                        chat_type TEXT NOT NULL DEFAULT 'private',
                        title TEXT DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'archived', 'deleted')),
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                ''')

                # Workspace Members Table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS workspace_members (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        telegram_user_id INTEGER NOT NULL,
                        username TEXT DEFAULT '',
                        display_name TEXT DEFAULT '',
                        role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner', 'admin', 'member', 'viewer')),
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('pending', 'active', 'suspended', 'removed')),
                        is_active INTEGER NOT NULL DEFAULT 1,
                        joined_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(workspace_id, telegram_user_id)
                    )
                ''')

                # Workspace Settings Table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS workspace_settings (
                        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(workspace_id, key)
                    )
                ''')

                # Workspace Invites Table (Hashed tokens, expiration, limits)
                cursor.execute('''
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
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_invites_ws ON workspace_invites(workspace_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_invites_hash ON workspace_invites(token_hash)')

                # Audit Logs Table (Immutable tenant actions log)
                cursor.execute('''
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
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_ws_created ON audit_logs(workspace_id, created_at DESC)')

                # Workspace Job Runs Table (Scheduler deduplication)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS workspace_job_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                        job_name TEXT NOT NULL,
                        scheduled_date TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'completed',
                        executed_at TEXT NOT NULL,
                        UNIQUE(workspace_id, job_name, scheduled_date)
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_runs_ws_job ON workspace_job_runs(workspace_id, job_name, scheduled_date)')

                # Dashboard Persistent Auth & Sessions Tables
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS dashboard_auth_codes (
                        code_hash TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        role TEXT NOT NULL DEFAULT 'member',
                        expires_at REAL NOT NULL,
                        used_at REAL DEFAULT NULL,
                        created_at REAL NOT NULL
                    )
                ''')
                cursor.execute('''
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
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_dash_sess_ws_user ON dashboard_sessions(workspace_id, user_id)')

                # Settings table (global legacy fallback & system-level settings)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT
                    )
                ''')

                # User Access Requests Table (Owner Approval System)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS access_requests (
                        telegram_user_id INTEGER PRIMARY KEY,
                        workspace_id TEXT DEFAULT NULL,
                        username TEXT DEFAULT '',
                        display_name TEXT DEFAULT '',
                        chat_id INTEGER NOT NULL,
                        chat_type TEXT NOT NULL DEFAULT 'private',
                        status TEXT NOT NULL DEFAULT 'pending',
                        requested_at TEXT NOT NULL,
                        reviewed_at TEXT DEFAULT NULL,
                        reviewed_by INTEGER DEFAULT NULL
                    )
                ''')
                
                # Custom Cafeteria Menu Items table (supports adding new veg dishes)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS custom_menu_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT,
                        name TEXT NOT NULL,
                        price REAL NOT NULL,
                        category TEXT DEFAULT 'Snacks & Tea',
                        is_veg INTEGER DEFAULT 1,
                        created_at TEXT,
                        UNIQUE(workspace_id, name)
                    )
                ''')
                
                # Payee Category Memory table (remembers user categorization preferences per payee)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payee_categories (
                        workspace_id TEXT,
                        payee_name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        updated_at TEXT,
                        PRIMARY KEY (workspace_id, payee_name)
                    )
                ''')

                # Recurring Payments table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS recurring_payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payee_name TEXT NOT NULL,
                        amount REAL NOT NULL,
                        category TEXT DEFAULT 'Bills & Utilities',
                        transaction_type TEXT DEFAULT 'SENT',
                        frequency TEXT NOT NULL DEFAULT 'MONTHLY',
                        interval_value INTEGER DEFAULT 1,
                        start_date DATE,
                        next_due_date DATE,
                        last_paid_date DATE DEFAULT NULL,
                        reminder_days_before INTEGER DEFAULT 1,
                        auto_log INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'ACTIVE',
                        notes TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                ''')
                
                # Migration checks for recurring_payments
                cursor.execute("PRAGMA table_info(recurring_payments)")
                r_cols = [row[1] for row in cursor.fetchall()]
                if "frequency" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN frequency TEXT DEFAULT 'MONTHLY'")
                if "start_date" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN start_date DATE")
                if "next_due_date" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN next_due_date DATE")
                if "last_paid_date" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN last_paid_date DATE DEFAULT NULL")
                if "status" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN status TEXT DEFAULT 'ACTIVE'")
                if "notes" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN notes TEXT")
                if "updated_at" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN updated_at TEXT")
                if "interval_value" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN interval_value INTEGER DEFAULT 1")
                if "reminder_days_before" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN reminder_days_before INTEGER DEFAULT 1")
                if "transaction_type" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN transaction_type TEXT DEFAULT 'SENT'")
                if "auto_log" not in r_cols:
                    cursor.execute("ALTER TABLE recurring_payments ADD COLUMN auto_log INTEGER DEFAULT 0")

                cursor.execute('CREATE INDEX IF NOT EXISTS idx_recurring_due ON recurring_payments (status, next_due_date)')
                
                # Undo log table (stores scoped undo actions in SQLite)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS undo_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT DEFAULT NULL,
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        uid TEXT NOT NULL,
                        snapshot_json TEXT DEFAULT NULL,
                        created_at TEXT NOT NULL,
                        used_at TEXT DEFAULT NULL
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_undo_chat_user ON undo_log(chat_id, user_id, used_at, created_at)')
                cursor.execute("PRAGMA table_info(undo_log)")
                _u_cols = [c['name'] for c in cursor.fetchall()]
                if "snapshot_json" not in _u_cols:
                    cursor.execute("ALTER TABLE undo_log ADD COLUMN snapshot_json TEXT DEFAULT NULL")

                # Monthly Reviews & Closing Table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS monthly_reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT,
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
                ''')

                # Pending Receipts table (persists pending transactions across bot reboots/restarts)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS pending_receipts (
                        pending_id TEXT PRIMARY KEY,
                        data_json TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Create indexes
                cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_uid ON transactions(uid)')
                # Drop legacy single-tenant unique reference index and enforce workspace-scoped uniqueness
                cursor.execute('DROP INDEX IF EXISTS idx_tx_ref_live')
                cursor.execute('''
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_ws_ref ON transactions(workspace_id, reference_number)
                    WHERE reference_number IS NOT NULL AND reference_number != '' AND deleted_at IS NULL
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_reference_number ON transactions(reference_number)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_date ON transactions(transaction_date)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_occurred_at ON transactions(occurred_at)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_type ON transactions(transaction_type)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON transactions(category)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_deleted_at ON transactions(deleted_at)')
                
                # Domain table workspace_id column migration checks
                for table_name in ['custom_menu_items', 'payee_categories', 'recurring_payments', 'undo_log', 'monthly_reviews', 'pending_receipts']:
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    t_cols = [row[1] for row in cursor.fetchall()]
                    if "workspace_id" not in t_cols:
                        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN workspace_id TEXT")

                # Check if monthly_reviews has old UNIQUE(year, month) constraint and rebuild
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='monthly_reviews'")
                mr_sql_row = cursor.fetchone()
                if mr_sql_row and mr_sql_row[0] and "UNIQUE(year, month)" in mr_sql_row[0]:
                    cursor.execute("""
                        CREATE TABLE monthly_reviews_v5 (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            workspace_id TEXT,
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
                            id, workspace_id, year, month, total_income, total_expense, net_savings, savings_rate_pct,
                            top_category, top_category_amount, top_payee, top_payee_amount,
                            max_transaction_id, max_transaction_amount, budget_allocated, budget_spent_pct,
                            is_closed, reviewed_at, notes, created_at
                        )
                        SELECT
                            id, workspace_id, year, month, total_income, total_expense, net_savings, savings_rate_pct,
                            top_category, top_category_amount, top_payee, top_payee_amount,
                            max_transaction_id, max_transaction_amount, budget_allocated, budget_spent_pct,
                            is_closed, reviewed_at, notes, created_at
                        FROM monthly_reviews
                    """)
                    cursor.execute("DROP TABLE monthly_reviews")
                    cursor.execute("ALTER TABLE monthly_reviews_v5 RENAME TO monthly_reviews")

                try:
                    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_ws_ym ON monthly_reviews(workspace_id, year, month)')
                except Exception as e:
                    logger.warning(f"Could not create unique index on monthly_reviews: {e}")
                try:
                    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_cmi_ws_name ON custom_menu_items(workspace_id, name)')
                except Exception as e:
                    logger.warning(f"Could not create unique index on custom_menu_items: {e}")

                # Idempotent Schema Migration v3: Backfill occurred_at and set updated_at to migration time in UTC
                from utils.dates import build_occurred_at, utc_now_iso
                cursor.execute("SELECT value FROM settings WHERE key = 'schema_version'")
                ver_row = cursor.fetchone()
                current_schema_ver = int(ver_row['value']) if ver_row and str(ver_row['value']).isdigit() else 0

                if current_schema_ver < 3:
                    cursor.execute("SELECT id, transaction_date, transaction_time FROM transactions WHERE occurred_at IS NULL OR occurred_at = ''")
                    backfill_rows = cursor.fetchall()
                    for r in backfill_rows:
                        occ = build_occurred_at(r['transaction_date'], r['transaction_time'])
                        cursor.execute("UPDATE transactions SET occurred_at = ? WHERE id = ?", (occ, r['id']))

                    mig_time = utc_now_iso()
                    cursor.execute("UPDATE transactions SET updated_at = ?", (mig_time,))
                    cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('schema_version', '3', ?)", (mig_time,))
                    logger.info(f"Executed schema migration v3: backfilled occurred_at for {len(backfill_rows)} rows, stamped updated_at with {mig_time}")

                now_utc = utc_now_iso()

                # Idempotent Schema Migration v4: Multi-Tenant Workspace Provisioning & Domain Backfill
                if current_schema_ver < 4:
                    from config import TELEGRAM_USER_ID, TELEGRAM_GROUP_ID
                    cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
                    ws_row = cursor.fetchone()
                    if ws_row and ws_row['value']:
                        default_ws_id = str(ws_row['value'])
                    else:
                        default_ws_id = str(uuid.uuid4())
                        cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('default_workspace_id', ?, ?)", (default_ws_id, now_utc))

                    primary_chat_id = int(TELEGRAM_GROUP_ID or TELEGRAM_USER_ID or 0)
                    primary_owner_id = int(TELEGRAM_USER_ID or 0)
                    chat_type = 'group' if primary_chat_id < 0 else 'private'

                    cursor.execute("""
                        INSERT OR IGNORE INTO workspaces (id, chat_id, chat_type, title, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, 'Primary Workspace', 1, ?, ?)
                    """, (default_ws_id, primary_chat_id, chat_type, now_utc, now_utc))

                    if primary_owner_id:
                        cursor.execute("""
                            INSERT OR IGNORE INTO workspace_members (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                            VALUES (?, ?, '', 'Primary Owner', 'owner', 1, ?, ?)
                        """, (default_ws_id, primary_owner_id, now_utc, now_utc))

                    # Backfill all existing rows in domain tables with default_ws_id
                    for t_name in ['transactions', 'custom_menu_items', 'payee_categories', 
                                  'recurring_payments', 'monthly_reviews', 'pending_receipts', 'undo_log']:
                        cursor.execute(f"UPDATE {t_name} SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = ''", (default_ws_id,))

                    # Copy all existing settings into workspace_settings for the default workspace
                    cursor.execute("SELECT key, value, updated_at FROM settings")
                    for s_row in cursor.fetchall():
                        cursor.execute("""
                            INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at)
                            VALUES (?, ?, ?, ?)
                        """, (default_ws_id, s_row['key'], s_row['value'], s_row['updated_at'] or now_utc))

                    # Create tenant-scoped indexes
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_chat_id ON workspaces(chat_id)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_members_ws_user ON workspace_members(workspace_id, telegram_user_id)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ws_settings_key ON workspace_settings(workspace_id, key)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tx_ws_occurred ON transactions(workspace_id, occurred_at)")
                    cursor.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_ws_ref ON transactions(workspace_id, reference_number)
                        WHERE reference_number IS NOT NULL AND reference_number != '' AND deleted_at IS NULL
                    """)
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rec_ws_due ON recurring_payments(workspace_id, status, next_due_date)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_undo_ws_user ON undo_log(workspace_id, user_id, created_at)")

                    cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('schema_version', '4', ?)", (now_utc,))
                    logger.info(f"Executed schema migration v4: provisioned default workspace {default_ws_id}, backfilled domain tables, and mirrored settings.")

                # Idempotent Schema Migration v5: Status columns, Access Requests scoping, & persistent auth
                if current_schema_ver < 5:
                    cursor.execute("PRAGMA table_info(workspaces)")
                    ws_cols = [c['name'] for c in cursor.fetchall()]
                    if "status" not in ws_cols:
                        cursor.execute("ALTER TABLE workspaces ADD COLUMN status TEXT DEFAULT 'active'")
                        cursor.execute("UPDATE workspaces SET status = 'active' WHERE status IS NULL OR status = ''")

                    cursor.execute("PRAGMA table_info(workspace_members)")
                    wm_cols = [c['name'] for c in cursor.fetchall()]
                    if "status" not in wm_cols:
                        cursor.execute("ALTER TABLE workspace_members ADD COLUMN status TEXT DEFAULT 'active'")
                        cursor.execute("UPDATE workspace_members SET status = 'active' WHERE status IS NULL OR status = ''")

                    cursor.execute("PRAGMA table_info(access_requests)")
                    ar_cols = [c['name'] for c in cursor.fetchall()]
                    if "workspace_id" not in ar_cols:
                        cursor.execute("ALTER TABLE access_requests ADD COLUMN workspace_id TEXT DEFAULT NULL")

                    cursor.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('schema_version', '5', ?)", (now_utc,))
                    logger.info("Executed schema migration v5: added workspace/member status and access_requests workspace_id.")

                # Ensure permanent database_id exists
                cursor.execute("SELECT value FROM settings WHERE key = 'database_id'")
                db_id_row = cursor.fetchone()
                if not db_id_row:
                    cursor.execute("INSERT INTO settings (key, value, updated_at) VALUES ('database_id', ?, ?)", (str(uuid.uuid4()), now_utc))

                # Ensure default settings exist without overwriting live values
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('initial_balance', '0.0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('current_balance', '0.0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('monthly_budget', '0.0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('backup_revision', '1', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('is_dirty', '0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('last_backup_ok_revision', '0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('telegram_backup_message_ids', '[]', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('last_local_backup_at', '', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('last_telegram_backup_at', '', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('last_drive_backup_at', '', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('database_initialized', '0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('schema_version', '5', now_utc))

                # Ensure all legacy transactions have default workspace assigned
                cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
                def_ws_row = cursor.fetchone()
                if def_ws_row and def_ws_row['value']:
                    d_id = str(def_ws_row['value'])
                    cursor.execute("UPDATE transactions SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = ''", (d_id,))
                
                # If transactions already exist, ensure database is marked initialized
                cursor.execute("SELECT COUNT(*) FROM transactions")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("UPDATE settings SET value = '1' WHERE key = 'database_initialized' AND value = '0'")
                
                conn.commit()
                logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
            raise

def get_custom_menu_items(workspace_id: str = None) -> list:
    """Returns custom cafeteria menu items from the database with dual-read workspace fallback."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if workspace_id:
            cursor.execute(
                "SELECT id, name, price, category, is_veg, created_at FROM custom_menu_items WHERE (workspace_id = ? OR workspace_id IS NULL) ORDER BY id ASC",
                (str(workspace_id),)
            )
        else:
            cursor.execute("SELECT id, name, price, category, is_veg, created_at FROM custom_menu_items ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]

def delete_custom_menu_item_by_id(item_id: int, workspace_id: str = None):
    """Deletes a custom cafeteria menu item by its ID with optional workspace scoping. Returns (success, item_name)."""
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if workspace_id:
                cursor.execute("SELECT name FROM custom_menu_items WHERE id = ? AND (workspace_id = ? OR workspace_id IS NULL)", (item_id, str(workspace_id)))
            else:
                cursor.execute("SELECT name FROM custom_menu_items WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Item not found"
            name = row['name']
            from database.queries import increment_revision_and_mark_dirty
            if workspace_id:
                cursor.execute("DELETE FROM custom_menu_items WHERE id = ? AND (workspace_id = ? OR workspace_id IS NULL)", (item_id, str(workspace_id)))
            else:
                cursor.execute("DELETE FROM custom_menu_items WHERE id = ?", (item_id,))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
        try:
            from services.backup_service import export_database_to_json
            export_database_to_json()
        except Exception as e:
            logger.warning(f"Backup after custom menu deletion failed: {e}")
        return True, name


