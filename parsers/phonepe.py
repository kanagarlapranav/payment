import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name, split_camel_case
from database.models import Transaction
from utils.currency import parse_amount, extract_amounts_from_line


class PhonePeParser(GenericParser):
    """Parser specifically tuned for PhonePe screenshots and payment shares."""
    
    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        if "bhim" in text_lower or "banking name" in text_lower:
            return False
        # PhonePe indicators
        return (
            "phonepe" in text_lower or "@ybl" in text_lower or "@ibl" in text_lower or
            "@axl" in text_lower or "phonepe transaction id" in text_lower
        )

    def parse(self) -> Transaction:
        # Start with generic extraction as baseline
        t = super().parse()
        t.payment_app = "PhonePe"
        
        lines = self.lines
        text_lower = self.raw_text.lower()
        
        # 1. Type Detection
        if "paid to" in text_lower or "paid successfully" in text_lower or "paidsuccessfully" in text_lower or "transferred to" in text_lower:
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        elif "received from" in text_lower or "money received" in text_lower or "credited to" in text_lower:
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"

        # 2. Extract Person Names
        for i, line in enumerate(lines):
            line_l = line.lower().strip()
            
            # Received from <Name> or line right after "Received from"
            if "received from" in line_l:
                part = re.sub(r'^.*?received\s+from\s*[:.-]*\s*', '', line, flags=re.IGNORECASE).strip()
                if part and not re.search(r'\d{4,}', part) and '@' not in part:
                    c = clean_person_name(part)
                    if c:
                        t.sender_name = c
                elif i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.search(r'^\d{4,}', next_l) and '@' not in next_l and '₹' not in next_l:
                        c = clean_person_name(next_l)
                        if c:
                            t.sender_name = c
                            
            # Paid to <Name> or line right after "Paid to" / "To"
            elif "paid to" in line_l:
                part = re.sub(r'^.*?paid\s+to\s*[:.-]*\s*', '', line, flags=re.IGNORECASE).strip()
                if part and not re.search(r'\d{4,}', part) and '@' not in part:
                    c = clean_person_name(part)
                    if c:
                        t.recipient_name = c
                elif i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.search(r'^\d{4,}', next_l) and '@' not in next_l and '₹' not in next_l:
                        c = clean_person_name(next_l)
                        if c:
                            t.recipient_name = c

        # 3. PhonePe Header Name Check (e.g. Rahul at top card)
        if not t.person_name and len(lines) > 0:
            for line in lines[:4]:
                line_l = line.lower()
                if any(k in line_l for k in ('phonepe', 'paid', 'received', 'success', 'transaction', 'http', 'download', 'credited', 'debited', 'transfer')):
                    continue
                if '@' in line or re.search(r'\d', line) or '₹' in line:
                    continue
                cleaned = clean_person_name(line)
                if cleaned:
                    if t.transaction_type == "SENT":
                        t.recipient_name = cleaned
                    else:
                        t.sender_name = cleaned
                    break

        # Set counterparty
        if t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name
        else:
            t.person_name = t.sender_name or t.person_name

        # 4. Extract UPI ID
        for line in lines:
            upi_match = re.search(r'([a-zA-Z0-9*.\-_]+@(?:ybl|ibl|axl|upi|ptyes|paytm|okhdfcbank|okaxis|okicici|oksbi))', line, re.IGNORECASE)
            if upi_match and not t.upi_id:
                t.upi_id = upi_match.group(1)

        # 5. Extract UTR / PhonePe Transaction ID
        for line in lines:
            utr_m = re.search(r'utr[\s:.-]*([0-9]{12})', line, re.IGNORECASE)
            if utr_m:
                t.reference_number = utr_m.group(1)
                break
        if not t.reference_number:
            for line in lines:
                tx_m = re.search(r'phonepe\s*transaction\s*id[\s:.-]*([a-zA-Z0-9]{12,24})', line, re.IGNORECASE)
                if tx_m:
                    t.reference_number = tx_m.group(1)
                    break

        return t
