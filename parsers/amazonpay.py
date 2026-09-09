import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name, split_camel_case
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount, extract_amounts_from_line


class AmazonPayParser(GenericParser):
    """Parser specifically tuned for Amazon Pay UPI payment receipts and screenshots."""

    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        return (
            "amazon pay" in text_lower or "@apl" in text_lower or "@rapl" in text_lower or
            "amazon reference id" in text_lower or "amazonpay" in text_lower
        )

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "Amazon Pay"
        lines = self.lines
        text_lower = self.raw_text.lower()

        # 1. Type Detection
        if "paid successfully" in text_lower or "paid to" in text_lower or "paid from" in text_lower:
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        elif "received" in text_lower or "money received" in text_lower:
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"

        # 2. Extract Amount (Amazon Pay puts ₹5,000 prominently right below 'Paid successfully')
        for i, line in enumerate(lines):
            line_l = line.lower()
            if "paid successfully" in line_l or "received successfully" in line_l:
                for offset in (0, 1, 2):
                    if i + offset < len(lines):
                        cand = lines[i + offset].strip()
                        cands = extract_amounts_from_line(cand)
                        valid = [c for c in cands if not self.is_invalid_amount(c, cand)]
                        if valid:
                            t.amount = valid[0]
                            break
                if t.amount > 0:
                    break

        # 3. Extract Recipient from 'Paid to' block (handling avatar badge e.g. KS on next line)
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if line_clean.lower().startswith('paid to'):
                # Check same line
                first_part = re.sub(r'^paid\s*to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                c = clean_person_name(first_part)
                if c:
                    t.recipient_name = c
                    break
                # Check next lines (skipping 1-2 letter avatar badges like 'KS')
                for offset in (1, 2, 3):
                    if i + offset < len(lines):
                        next_l = lines[i + offset].strip()
                        if '@' in next_l or any(k in next_l.lower() for k in ('paid', 'from', 'ref', 'upi', 'date', 'bank')):
                            continue
                        c = clean_person_name(next_l)
                        if c and len(c.replace(' ', '').replace('.', '')) > 2:
                            t.recipient_name = c
                            break
                if t.recipient_name:
                    break

        # 4. Extract Sender / Bank from 'Paid from' block
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if line_clean.lower().startswith('paid from'):
                for offset in (1, 2, 3, 4):
                    if i + offset < len(lines):
                        next_l = lines[i + offset].strip()
                        # Look for bank name e.g. "State Bank of India ****7751"
                        if any(b in next_l.lower() for b in ('bank', 'sbi', 'hdfc', 'icici', 'axis', 'union', 'kotak', 'pnb', 'bob')):
                            bank_clean = re.sub(r'[*xX\d]+$', '', next_l).strip(' -–:')
                            t.bank_name = bank_clean.title()
                            acc_m = re.search(r'[*xX\s]+(\d{3,6})\b', next_l)
                            if acc_m:
                                t.bank_account = acc_m.group(1)
                        # Look for sender UPI ID e.g. 8688701141@apl
                        upi_m = re.search(r'([a-zA-Z0-9*.\-_]+@(?:apl|rapl|ybl|ibl|axl|upi|paytm))', next_l, re.IGNORECASE)
                        if upi_m and not t.upi_id:
                            t.upi_id = upi_m.group(1)

        # 5. Extract UPI Transaction ID (12-digit UTR)
        for line in lines:
            if "upi transaction id" in line.lower() or "transaction id" in line.lower():
                ref_m = re.search(r'\b([0-9]{12})\b', line)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break
        if not t.reference_number:
            for line in lines:
                ref_m = re.search(r'(?:ref(?:\s*no)?|utr)[\s:.-]*([0-9]{12})', line, re.IGNORECASE)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break

        # 6. Extract Date & Time (e.g. "9 Sept 2026, 12:17 PM" or "Date and time 9 Sept 2026, 12:17 PM")
        for line in lines:
            if "date" in line.lower() or "time" in line.lower() or re.search(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', line, re.IGNORECASE):
                d_match = re.search(r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)', line, re.IGNORECASE)
                if d_match:
                    t.transaction_date = parse_date(d_match.group(1))
                t_match = re.search(r'(\d{1,2}[:.]\d{2}\s*(?:[aApP][mM])?)', line)
                if t_match:
                    t.transaction_time = parse_time(t_match.group(1))
                if t.transaction_date:
                    break

        # Set consolidated person_name
        if t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name
        else:
            t.person_name = t.sender_name or t.person_name

        return t
