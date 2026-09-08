import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount


class BhimParser(GenericParser):
    """Parser specifically tuned for BHIM UPI payment screenshots and receipts."""

    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        if "paytm" in text_lower or "phonepe" in text_lower or "google pay" in text_lower:
            return False
        return "bhim" in text_lower or "banking name" in text_lower or "payment initiated by" in text_lower

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "BHIM"

        text_lower = self.raw_text.lower()

        # 1. Transaction Type & Status
        if "paid" in text_lower or "send money" in text_lower or "payment initiated" in text_lower:
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        elif "received" in text_lower or "money received" in text_lower:
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"

        # 2. Amount:
        # In BHIM screenshots, amount is prominently below "Paid" or "✔ Paid" (e.g. ?4,000.00)
        for i, line in enumerate(self.lines):
            line_l = line.lower().strip()
            # Skip process detail lines
            if any(p in line_l for p in ('initiated', 'transferred', 'received by')):
                continue
            if re.search(r'\b(?:paid|received)\b', line_l):
                for offset in (0, 1, 2, 3):
                    if i + offset < len(self.lines):
                        cand = self.lines[i + offset].strip()
                        if self._is_promo_or_balance_line(cand):
                            continue
                        if any(w in cand.lower() for w in ('bank', 'upi', 'from', 'to', 'ref', 'rupees', 'only', 'initiated', 'seconds')):
                            continue
                        m = re.search(r'(?:[₹$€£?]|Rs\.?|INR|[RrFf](?=\d))?\s*(\b(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?\b)', cand, re.IGNORECASE)
                        if m:
                            val = parse_amount(m.group(0))
                            if val >= 1.0:
                                t.amount = val
                                break
                if t.amount > 0:
                    break

        # 3. Person Names
        # Sender: "Payment initiated by <Sender>"
        init_m = re.search(r'payment\s+initiated\s+by\s+([a-zA-Z\s.]+?)(?:\'s|\n|$)', self.raw_text, re.IGNORECASE)
        if init_m:
            s_name = clean_person_name(init_m.group(1))
            if s_name:
                t.sender_name = s_name

        # Recipient: "Payment received by <Recipient>"
        rec_m = re.search(r'payment\s+received\s+by\s+([a-zA-Z\s.\n]+?)(?:hide\s+details|share|more\s+details|\n\n|$)', self.raw_text, re.IGNORECASE)
        if rec_m:
            r_name = re.sub(r'\s+', ' ', rec_m.group(1)).strip()
            r_name = clean_person_name(r_name)
            if r_name:
                t.recipient_name = r_name

        # Also check "Banking Name"
        for i, line in enumerate(self.lines):
            if "banking name" in line.lower() and i + 1 < len(self.lines):
                b_name = clean_person_name(self.lines[i + 1])
                if b_name:
                    if not t.recipient_name:
                        t.recipient_name = b_name
                    elif ' ' not in t.recipient_name and ' ' in b_name:
                        t.recipient_name = b_name
                break

        # Set primary person_name based on transaction type
        if t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name
        else:
            t.person_name = t.sender_name or t.person_name

        # 4. Bank details
        # Look for bank name (e.g. State Bank Of India) and account number (e.g. XXXX7751, X7751)
        for line in self.lines:
            line_s = line.strip()
            if any(b in line_s.lower() for b in ('bank', 'sbi', 'hdfc', 'icici', 'axis', 'kotak', 'pnb', 'bob', 'canara', 'union')):
                if not any(k in line_s.lower() for k in ('account', 'mode', 'instrument', 'banking name')):
                    t.bank_name = line_s
            if re.search(r'\b[Xx*]+\d{3,4}\b', line_s):
                t.bank_account = line_s

        # 5. Reference Number
        # Transaction ID 134446412863
        for i, line in enumerate(self.lines):
            if "transaction id" in line.lower():
                same_line = re.search(r'\b(\d{12})\b', line)
                if same_line:
                    t.reference_number = same_line.group(1)
                elif i + 1 < len(self.lines):
                    next_line = re.search(r'\b(\d{12})\b', self.lines[i + 1])
                    if next_line:
                        t.reference_number = next_line.group(1)
                break

        # 6. UPI ID
        upi_match = re.search(r'([a-zA-Z0-9*.\-_]+@[a-zA-Z]{2,})', self.raw_text)
        if upi_match:
            t.upi_id = upi_match.group(1)

        # 7. Date & Time
        # E.g. "8th Sep 26, 01:44 pm" or "8th Sep 26, 12:37 pm"
        dt_match = re.search(
            r'(\d{1,2}(?:st|nd|rd|th)?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)[,\s]+(\d{1,2}[:.]\d{2}\s*(?:[aApP][mM])?)',
            self.raw_text, re.IGNORECASE
        )
        if dt_match:
            d_str = dt_match.group(1)
            t_str = dt_match.group(2)
            t.transaction_date = parse_date(d_str)
            t.transaction_time = parse_time(t_str)

        return t
