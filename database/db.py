import sqlite3
import threading
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
                        deleted_at TIMESTAMP DEFAULT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Migration checks: Ensure category, uid, deleted_at columns exist
                cursor.execute("PRAGMA table_info(transactions)")
                columns = [row[1] for row in cursor.fetchall()]
                if "category" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN category TEXT DEFAULT 'General'")
                if "uid" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN uid TEXT")
                cursor.execute("UPDATE transactions SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL OR uid = ''")
                if "deleted_at" not in columns:
                    cursor.execute("ALTER TABLE transactions ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL")
                
                # Settings table (for balance, budget, digest)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Payee Category Memory table (remembers user categorization preferences per payee)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS payee_categories (
                        payee_name TEXT PRIMARY KEY,
                        category TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Recurring Payments table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS recurring_payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payee_name TEXT NOT NULL,
                        amount REAL NOT NULL,
                        day_of_month INTEGER NOT NULL,
                        category TEXT DEFAULT 'Bills & Utilities',
                        is_active INTEGER DEFAULT 1,
                        last_notified DATE,
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
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_type ON transactions(transaction_type)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON transactions(category)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_deleted_at ON transactions(deleted_at)')
                
                # Ensure default settings exist without overwriting live values
                cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('initial_balance', '0.0'))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('current_balance', '0.0'))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('monthly_budget', '0.0'))
                cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('backup_revision', '1'))
                
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
            cursor.execute("DELETE FROM custom_menu_items WHERE id = ?", (item_id,))
            conn.commit()
        try:
            from services.backup_service import export_database_to_json
            export_database_to_json()
        except Exception as e:
            logger.warning(f"Backup after custom menu deletion failed: {e}")
        return True, name


