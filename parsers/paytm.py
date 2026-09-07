import re
from parsers.generic import GenericParser
from parsers.base import split_camel_case, clean_person_name
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
            if any(k in line_lower for k in ("money received", "moneyreceived", "payment received", "paymentreceived", "received successfully")):
                t.transaction_type = "RECEIVED"
                t.payment_status = "SUCCESS"
                # Check immediate next line for amount (e.g. ₹600 or 600) only if not already set or candidate is valid
                if (not t.amount or t.amount <= 0) and i + 1 < len(self.lines):
                    next_l = self.lines[i + 1].strip()
                    if not any(k in next_l.lower() for k in ('ref', 'upi', 'bank', 'from', 'to', 'am', 'pm', 'sep', 'aug')):
                        amt_m = re.search(r'(?:[₹$€£]|Rs\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)', next_l)
                        if amt_m:
                            val_str = amt_m.group(1).replace(',', '')
                            if (val_str.isdigit() or re.match(r'^\d+\.\d{1,2}$', val_str)) and len(val_str) < 8:
                                amt_val = float(val_str)
                                if amt_val > 0 and amt_val != 2026:
                                    t.amount = amt_val
            elif "received at" in line_lower or "receivedat" in line_lower:
                t.transaction_type = "RECEIVED"
                t.payment_status = "SUCCESS"
            elif "sent successfully" in line_lower or "paid successfully" in line_lower or "transferred successfully" in line_lower:
                t.transaction_type = "SENT"
                t.payment_status = "SUCCESS"

            # Check From: block in Paytm
            if re.match(r'^from\b', line, re.IGNORECASE):
                first_part = re.sub(r'^from\s*[:.-]*\s*', '', line, flags=re.IGNORECASE).strip()
                name_parts = [first_part] if first_part else []
                # Collect wrapped name line before UPI ID
                if i + 1 < len(self.lines):
                    next_l = self.lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account)\b', next_l, re.IGNORECASE) and '@' not in next_l and not re.search(r'\d{3,}', next_l):
                        name_parts.append(next_l)
                full_from = clean_person_name(' '.join(name_parts))
                if full_from:
                    t.sender_name = full_from

            # Check To: block in Paytm
            elif re.match(r'^to\b', line, re.IGNORECASE):
                first_part = re.sub(r'^to\s*[:.-]*\s*', '', line, flags=re.IGNORECASE).strip()
                name_parts = [first_part] if first_part else []
                # Collect wrapped name line before UPI ID
                if i + 1 < len(self.lines):
                    next_l = self.lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account)\b', next_l, re.IGNORECASE) and '@' not in next_l and not re.search(r'\d{3,}', next_l):
                        name_parts.append(next_l)
                full_to = clean_person_name(' '.join(name_parts))
                if full_to:
                    t.recipient_name = full_to

            # Reference number (Paytm uses 'UPI Ref No' or 'UPIRefNo')
            if not t.reference_number:
                ref_match = re.search(r'upi\s*ref\s*no[\s:.-]*([0-9]{12})', line_lower)
                if ref_match:
                    t.reference_number = ref_match.group(1)

            # UPI ID (e.g. ******1141@ptyes or user@paytm or sender's UPI)
            if not t.upi_id:
                upi_match = re.search(r'([a-zA-Z0-9*.\-_]+@(?:paytm|ptyes|ptaxis|pthdfc|ptsbi|upi|ybl|ibl|axl))', line, re.IGNORECASE)
                if upi_match:
                    t.upi_id = upi_match.group(1)

            # Bank extraction (e.g. Union Bank Of India - 1185, or on next line)
            if not t.bank_name and "bank" in line_lower:
                bank_part = line.strip()
                acc_match = re.search(r'[-–\s]+([0-9]{4})[a-zA-Z]*$', bank_part)
                if acc_match:
                    t.bank_account = acc_match.group(1)
                    bank_part = bank_part[:acc_match.start()]
                elif i + 1 < len(self.lines):
                    next_line = self.lines[i + 1].strip()
                    next_acc = re.search(r'^([0-9]{4})\b', next_line)
                    if next_acc:
                        t.bank_account = next_acc.group(1)
                cleaned_bank = split_camel_case(bank_part).strip(' -–:.,')
                t.bank_name = cleaned_bank.title()

        # Final person name mapping
        if t.transaction_type == "RECEIVED":
            t.person_name = t.sender_name or t.person_name
        elif t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name

        return t
