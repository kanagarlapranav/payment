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
                        occurred_at TEXT,
                        deleted_at TEXT DEFAULT NULL,
                        created_at TEXT,
                        updated_at TEXT
                    )
                ''')
                
                # Migration checks: Ensure category, uid, deleted_at, occurred_at columns exist
                cursor.execute("PRAGMA table_info(transactions)")
                columns = [row[1] for row in cursor.fetchall()]
                if "category" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN category TEXT DEFAULT 'General'")
                if "uid" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN uid TEXT")
                cursor.execute("UPDATE transactions SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL OR uid = ''")
                if "deleted_at" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN deleted_at TEXT DEFAULT NULL")
                if "occurred_at" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN occurred_at TEXT")
                
                # Settings table (for balance, budget, digest)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT
                    )
                ''')
                
                # Custom Cafeteria Menu Items table (supports adding new veg dishes)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS custom_menu_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        price REAL NOT NULL,
                        category TEXT DEFAULT 'Snacks & Tea',
                        is_veg INTEGER DEFAULT 1,
                        created_at TEXT
                    )
                ''')
                
                # Payee Category Memory table (remembers user categorization preferences per payee)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payee_categories (
                        payee_name TEXT PRIMARY KEY,
                        category TEXT NOT NULL,
                        updated_at TEXT
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
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        uid TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        used_at TEXT DEFAULT NULL
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_undo_chat_user ON undo_log(chat_id, user_id, used_at, created_at)')

                # Monthly Reviews & Closing Table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS monthly_reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                        UNIQUE(year, month)
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
                cursor.execute('''
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_ref_live ON transactions(reference_number)
                    WHERE reference_number IS NOT NULL AND reference_number != '' AND deleted_at IS NULL
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_reference_number ON transactions(reference_number)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_date ON transactions(transaction_date)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_occurred_at ON transactions(occurred_at)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_type ON transactions(transaction_type)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON transactions(category)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_deleted_at ON transactions(deleted_at)')
                
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
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('backup_blocked', '0', now_utc))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)', ('schema_version', '3', now_utc))
                
                # If transactions already exist, ensure database is marked initialized
                cursor.execute("SELECT COUNT(*) FROM transactions")
                if cursor.fetchone()[0] > 0:
                    cursor.execute("UPDATE settings SET value = '1' WHERE key = 'database_initialized' AND value = '0'")
                
                conn.commit()
                logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
            raise

def get_custom_menu_items() -> list:
    """Returns all custom cafeteria menu items from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, category, is_veg, created_at FROM custom_menu_items ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]

def delete_custom_menu_item_by_id(item_id: int):
    """Deletes a custom cafeteria menu item by its ID. Returns (success, item_name)."""
    with LEDGER_LOCK:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM custom_menu_items WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Item not found"
            name = row['name']
            from database.queries import increment_revision_and_mark_dirty
            cursor.execute("DELETE FROM custom_menu_items WHERE id = ?", (item_id,))
            increment_revision_and_mark_dirty(conn)
            conn.commit()
        try:
            from services.backup_service import export_database_to_json
            export_database_to_json()
        except Exception as e:
            logger.warning(f"Backup after custom menu deletion failed: {e}")
        return True, name


