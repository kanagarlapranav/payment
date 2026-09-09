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

APP_NAME_BLACKLIST = {
    'phonepe', 'phone pe', 'paytm', 'google pay', 'googlepay', 'gpay',
    'bhim', 'cred', 'amazon pay', 'bank', 'upi', 'payment', 'phonepe payment',
    'paytm payments bank', 'state bank of india', 'union bank of india',
    'hdfc bank', 'icici bank', 'axis bank', 'money sent', 'money received',
    'paid successfully', 'payment successful', 'transaction successful',
    'transfer successful', 'download latest app', 'view history', 'check balance',
    'transfer details', 'phonepe transaction id', 'transaction id', 'credited to',
    'debited from', 'payment details', 'utr', 'hide details', 'share', 'process details'
}

def clean_person_name(name: str) -> str:
    """Cleans extracted person name by removing noise keywords and punctuation."""
    if not name or '@' in name:
        return ""
    # Remove leading/trailing non-alphanumeric except spaces and periods
    cleaned = re.sub(r'^[^a-zA-Z0-9]+|[^a-zA-Z0-9\s.]+$', '', name)
    # Remove common noise prefixes with word boundaries
    noise_prefixes = [
        'paid to', 'received from', 'transfer to', 'transferred to', 'payment to',
        'money sent to', 'money received from', 'to', 'from', 'edit', 'pay',
        'view history', 'payment from', 'sent to'
    ]
    for n in noise_prefixes:
        cleaned = re.sub(rf'^{n}\b\s*[:.-]*\s*', '', cleaned, flags=re.IGNORECASE)
        
    cleaned = split_camel_case(cleaned)
    # Remove trailing badge letters/digits (like 'PV', '8', 'LV')
    cleaned = re.sub(r'\s+[A-Z0-9]{1,2}$', '', cleaned)
    # Remove trailing noise words like 'via', 'from', 'to', 'on', 'using', 'on phonepe', 'via paytm'
    cleaned = re.sub(r'\s+(?:via|from|to|on|using|for)\s+(?:phonepe|paytm|gpay|google pay|upi|bhim|bank).*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+(?:via|from|to|on|using)\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(' -–:.,')
    
    # If it's a short noise string (like "LV" or single letter), return empty
    if len(cleaned.replace(' ', '').replace('.', '')) <= 2 and cleaned.isupper():
        return ""
    if cleaned.lower() in APP_NAME_BLACKLIST:
        return ""
    # If string contains digits and is not a legitimate name
    if re.search(r'\d{3,}', cleaned):
        return ""
    return cleaned.title()

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
