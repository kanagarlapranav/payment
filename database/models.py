from dataclasses import dataclass
from typing import Optional, Any
from datetime import datetime, date

_UNSET = object()

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
    category: str = "General"
    balance_before: float = 0.0
    balance_after: float = 0.0
    ocr_text: str = ""
    original_image_path: str = ""
    telegram_message_id: str = ""
    telegram_chat_id: str = ""
    telegram_user_id: Optional[int] = None
    uid: Optional[str] = None
    workspace_id: Any = _UNSET
    occurred_at: Optional[str] = None
    deleted_at: Optional[datetime] = None
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

@dataclass
class Workspace:
    """Represents an isolated multi-tenant workspace anchored to a Telegram chat."""
    id: str
    chat_id: int
    chat_type: str = "private"
    title: str = ""
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass
class WorkspaceMember:
    """Represents a member with a role in a workspace."""
    id: Optional[int] = None
    workspace_id: str = ""
    telegram_user_id: int = 0
    username: str = ""
    display_name: str = ""
    role: str = "member"  # 'owner', 'admin', 'member', 'viewer'
    is_active: bool = True
    joined_at: Optional[str] = None
    updated_at: Optional[str] = None
