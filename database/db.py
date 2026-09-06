import sqlite3
from config import DB_PATH, logger

def get_db_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

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
                    balance_before REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    ocr_text TEXT,
                    original_image_path TEXT,
                    telegram_message_id TEXT,
                    telegram_chat_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Settings table (for balance)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_reference_number ON transactions(reference_number)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_date ON transactions(transaction_date)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_transaction_type ON transactions(transaction_type)')
            
            # Initialize balance if not exists
            cursor.execute('SELECT value FROM settings WHERE key = ?', ('current_balance',))
            if not cursor.fetchone():
                cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('current_balance', '0.0'))
                cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('initial_balance', '0.0'))
            
            conn.commit()
            logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error setting up database: {e}")
        raise
