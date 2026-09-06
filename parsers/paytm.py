import re
from parsers.generic import GenericParser
from parsers.base import split_camel_case
from database.models import Transaction

class PaytmParser(GenericParser):
    """Parser specifically tuned for Paytm screenshots."""
    
    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        return "paytm" in text_lower or "@paytm" in text_lower or "@ptyes" in text_lower or "@ptaxis" in text_lower or "@pthdfc" in text_lower or "@ptsbi" in text_lower

    def parse(self) -> Transaction:
        # Start with generic extraction
        t = super().parse()
        t.payment_app = "Paytm"
        
        for i, line in enumerate(self.lines):
            line_lower = line.lower()
            
            # Paytm specific type logic
            if "money received" in line_lower or "moneyreceived" in line_lower or "received successfully" in line_lower or "payment received" in line_lower or "received at" in line_lower or "receivedat" in line_lower:
                t.transaction_type = "RECEIVED"
                t.payment_status = "SUCCESS"
            elif "sent successfully" in line_lower or "paid successfully" in line_lower or "transferred successfully" in line_lower:
                t.transaction_type = "SENT"
                t.payment_status = "SUCCESS"
                
            # Reference number (Paytm uses 'UPI Ref No' or 'UPIRefNo')
            if not t.reference_number:
                ref_match = re.search(r'upi\s*ref\s*no[\s:.-]*([0-9]{12})', line_lower)
                if ref_match:
                    t.reference_number = ref_match.group(1)

            # UPI ID (e.g. ******1141@ptyes or user@paytm)
            if not t.upi_id:
                upi_match = re.search(r'([a-zA-Z0-9*.\-_]+@(?:paytm|ptyes|ptaxis|pthdfc|ptsbi|upi))', line, re.IGNORECASE)
                if upi_match:
                    t.upi_id = upi_match.group(1)

            # Bank extraction (e.g. UnionBankOf India-1185M or State Bank of India - 1234, or on next line)
            if not t.bank_name and "bank" in line_lower:
                bank_part = line.strip()
                # Split account number if present (e.g. -1185 or XX1234)
                acc_match = re.search(r'[-–\s]+([0-9]{4})[a-zA-Z]*$', bank_part)
                if acc_match:
                    t.bank_account = acc_match.group(1)
                    bank_part = bank_part[:acc_match.start()]
                elif i + 1 < len(self.lines):
                    next_line = self.lines[i + 1].strip()
                    next_acc = re.search(r'^([0-9]{4})\b', next_line)
                    if next_acc:
                        t.bank_account = next_acc.group(1)
                # Clean bank name
                cleaned_bank = split_camel_case(bank_part).strip(' -–:.,')
                t.bank_name = cleaned_bank.title()

        # Update person name
        if t.transaction_type == "RECEIVED":
            t.person_name = t.sender_name or t.person_name
        elif t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name

        return t
