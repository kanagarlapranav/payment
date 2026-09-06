import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name, split_camel_case
from database.models import Transaction

class PhonePeParser(GenericParser):
    """Parser specifically tuned for PhonePe screenshots."""
    
    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        if "paytm" in text_lower:
            return False
        return "phonepe" in text_lower or "@ybl" in text_lower or "@ibl" in text_lower or "@axl" in text_lower

    def parse(self) -> Transaction:
        # Start with generic extraction
        t = super().parse()
        t.payment_app = "PhonePe"
        
        lines = self.lines
        
        for i, line in enumerate(lines):
            line_lower = line.lower()
            
            # Look for exact PhonePe success messages
            if "paid successfully" in line_lower or "paidsuccessfully" in line_lower:
                t.transaction_type = "SENT"
                t.payment_status = "SUCCESS"
            elif "received from" in line_lower or "money received" in line_lower:
                t.transaction_type = "RECEIVED"
                t.payment_status = "SUCCESS"
            
            # UPI extraction (e.g. 9876543210@ybl)
            upi_match = re.search(r'([a-zA-Z0-9*.\-_]+@(?:ybl|ibl|axl|upi))', line, re.IGNORECASE)
            if upi_match and not t.upi_id:
                t.upi_id = upi_match.group(1)
        
        # Check PhonePe header name placement
        if not t.person_name and len(lines) > 0:
            for line in lines[:3]:
                if "phonepe" not in line.lower() and "₹" not in line and "paid" not in line.lower() and "received" not in line.lower():
                    cleaned = clean_person_name(line)
                    if cleaned:
                        t.person_name = cleaned
                        if t.transaction_type == "SENT":
                            t.recipient_name = cleaned
                        elif t.transaction_type == "RECEIVED":
                            t.sender_name = cleaned
                        break
                     
        return t
