from dataclasses import dataclass
from typing import Optional
from datetime import datetime, date

@dataclass
class Transaction:
    """Represents a financial transaction."""
    id: Optional[int] = None
    transaction_type: str = "" # 'SENT' or 'RECEIVED'
    amount: float = 0.0
    person_name: str = ""
    sender_name: str = ""
    recipient_name: str = ""
    upi_id: str = ""
    phone_number: str = ""
    transaction_date: Optional[date] = None
    transaction_time: str = ""
    reference_number: str = ""
    transaction_id: str = ""
    payment_app: str = ""
    bank_name: str = ""
    bank_account: str = ""
    payment_status: str = ""
    balance_before: float = 0.0
    balance_after: float = 0.0
    ocr_text: str = ""
    original_image_path: str = ""
    telegram_message_id: str = ""
    telegram_chat_id: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class TransactionSummary:
    """Represents daily summary of transactions."""
    total_sent: float = 0.0
    total_received: float = 0.0
    net_change: float = 0.0
    transaction_count: int = 0
    current_balance: float = 0.0
