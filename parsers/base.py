import re
from abc import ABC, abstractmethod
from typing import Optional, List
from database.models import Transaction

def split_camel_case(text: str) -> str:
    """Splits concatenated words like 'LakkimsettiSaiSriVamsi' -> 'Lakkimsetti Sai Sri Vamsi'."""
    if not text:
        return ""
    # Insert space before capital letters if preceded by lower case or multiple capitals
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    return re.sub(r'\s+', ' ', s).strip()

def clean_person_name(name: str) -> str:
    """Cleans extracted person name by removing noise keywords and punctuation."""
    if not name or '@' in name:
        return ""
    # Remove leading/trailing non-alphanumeric except spaces
    cleaned = re.sub(r'^[^a-zA-Z0-9]+|[^a-zA-Z0-9\s.]+$', '', name)
    # Remove common noise words
    noise = ['paid to', 'received from', 'transfer to', 'payment to', 'money sent to', 'to', 'from', 'edit', 'pay', 'view history']
    for n in noise:
        cleaned = re.sub(rf'^{n}\s*[:.-]*\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = split_camel_case(cleaned)
    # If it's a short noise string (like "LV" or single letter), return empty
    if len(cleaned.replace(' ', '')) <= 2 and cleaned.isupper():
        return ""
    return cleaned.strip().title()

class BasePaymentParser(ABC):
    """Abstract base class for payment parsers."""
    
    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        self.lines = [line.strip() for line in raw_text.split('\n') if line.strip()]

    @abstractmethod
    def can_parse(self) -> bool:
        """Determines if this parser is suitable for the text."""
        pass

    @abstractmethod
    def parse(self) -> Transaction:
        """Parses the text and returns a populated Transaction object."""
        pass
    
    def get_confidence(self, transaction: Transaction) -> int:
        """Calculates a confidence score for the parsed transaction."""
        score = 0
        if transaction.amount > 0:
            score += 35
        if transaction.transaction_type in ['SENT', 'RECEIVED']:
            score += 25
        if transaction.reference_number:
            score += 15
        if transaction.transaction_date:
            score += 10
        if transaction.transaction_time:
            score += 5
        if transaction.person_name or transaction.sender_name or transaction.recipient_name:
            score += 10
            
        return min(score, 100)
