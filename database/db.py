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
            
            # Initialize balance and seed existing records if database is fresh
            cursor.execute('SELECT COUNT(*) FROM transactions')
            count = cursor.fetchone()[0]
            if count == 0:
                initial_transactions = [
                    (1, 'RECEIVED', 30700.0, 'Lakkimsetti Sai Sri Vamsi', 'Lakkimsetti Sai Sri Vamsi', 'Kanagarla Pranav', '******1141@ptyes', '', '2026-09-04', '10:02 AM', '661385614715', '', 'Paytm', 'Union Bank Of India', '1185', 'SUCCESS', 0.0, 30700.0, 'Money Received: 30,700 from Lakkimsetti Sai Sri Vamsi', '', '9', '-1004310685141', '2026-09-06 12:21:57', '2026-09-06 12:21:57'),
                    (2, 'RECEIVED', 6200.0, 'Johnson Siddhu Motru', 'Johnson Siddhu Motru', 'Kanagarla Pranav', '******1141@ptyes', '', '2026-09-04', '12:47 PM', '511616086345', '', 'Paytm', 'Union Bank Of India', '', 'SUCCESS', 30700.0, 36900.0, 'Money Received: 6,200 from Johnson Siddhu Motru', '', '21', '-1004310685141', '2026-09-06 12:29:23', '2026-09-06 12:29:23'),
                    (3, 'SENT', 5000.0, 'Balaji Icic Admin', '', 'Balaji Icic Admin', '', '', '2026-09-05', '', '', '', 'Generic', '', '', 'SUCCESS', 36900.0, 31900.0, 'Paid to balaji icic admin 5000', '', '33', '-1004310685141', '2026-09-06 15:00:52', '2026-09-06 15:00:52')
                ]
                cursor.executemany('''
                    INSERT INTO transactions (
                        id, transaction_type, amount, person_name, sender_name, recipient_name,
                        upi_id, phone_number, transaction_date, transaction_time, reference_number,
                        transaction_id, payment_app, bank_name, bank_account, payment_status,
                        balance_before, balance_after, ocr_text, original_image_path,
                        telegram_message_id, telegram_chat_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', initial_transactions)

            cursor.execute('SELECT value FROM settings WHERE key = ?', ('current_balance',))
            if not cursor.fetchone():
                cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('current_balance', '31900.0'))
                cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('initial_balance', '0.0'))
            
            conn.commit()
            logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error setting up database: {e}")
        raise

