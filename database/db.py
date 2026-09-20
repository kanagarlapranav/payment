import sqlite3
from contextlib import contextmanager
from config import DB_PATH, logger

@contextmanager
def get_db_connection():
    """Returns a connection to the SQLite database with automatic commit and close."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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

