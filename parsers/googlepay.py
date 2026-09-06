import re
from parsers.generic import GenericParser
from database.models import Transaction

class GooglePayParser(GenericParser):
    """Parser specifically tuned for Google Pay screenshots."""
    
    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        return "gpay" in text_lower or "google pay" in text_lower or "@okaxis" in text_lower or "@okhdfcbank" in text_lower or "@okicici" in text_lower or "@oksbi" in text_lower or "@axisbank" in text_lower

    def parse(self) -> Transaction:
        # Start with generic extraction
        t = super().parse()
        t.payment_app = "Google Pay"
        
        text_lower = self.raw_text.lower()
        
        for line in self.lines:
            line_lower = line.lower()
            
            # Type detection
            if "paid to" in line_lower or "payment to" in line_lower:
                t.transaction_type = "SENT"
                t.payment_status = "SUCCESS"
            elif "received from" in line_lower or "money received" in line_lower:
                t.transaction_type = "RECEIVED"
                t.payment_status = "SUCCESS"
                
            # Bank name extraction
            bank_match = re.search(r'(state bank of india|hdfc bank|icici bank|axis bank|union bank of india|punjab national bank|bank of baroda|canara bank|kotak)', line_lower)
            if bank_match and not t.bank_name:
                t.bank_name = bank_match.group(1).title()
                
            # UPI ID
            upi_match = re.search(r'([a-zA-Z0-9*.\-_]+@ok[a-zA-Z]+)', line, re.IGNORECASE)
            if upi_match and not t.upi_id:
                t.upi_id = upi_match.group(1)

        return t
