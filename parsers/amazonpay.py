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
        t = Transaction()
        t.ocr_text = self.raw_text
        t.payment_app = "Amazon Pay"
        lines = self.lines
        text_lower = self.raw_text.lower()

        # 1. Type Detection
        if any(k in text_lower for k in ('paid successfully', 'paid to', 'pald to', 'paid from', 'payment to', 'payment successful', 'sent to')):
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        elif any(k in text_lower for k in ('received', 'money received', 'credited to')):
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"
        else:
            t.transaction_type = "SENT"

        # 2. Extract Amount (prominently right below 'Paid successfully' / 'Payment successful')
        for i, line in enumerate(lines):
            line_l = line.lower()
            if any(k in line_l for k in ('paid successfully', 'payment successful', 'received successfully')):
                for offset in (0, 1, 2, 3):
                    if i + offset < len(lines):
                        cand = lines[i + offset].strip()
                        if self._is_promo_or_balance_line(cand):
                            continue
                        cands = extract_amounts_from_line(cand)
                        valid = [c for c in cands if not self.is_invalid_amount(c, cand)]
                        if valid:
                            t.amount = valid[0]
                            break
                if t.amount > 0:
                    break

        if t.amount <= 0:
            for line in lines[:10]:
                if self._is_promo_or_balance_line(line) or any(w in line.lower() for w in ('upi', 'id', 'ref', 'date', 'bank', '****', '***')):
                    continue
                cands = extract_amounts_from_line(line)
                valid = [c for c in cands if not self.is_invalid_amount(c, line)]
                if valid:
                    t.amount = valid[0]
                    break

        # 3. Extract Recipient from 'Paid to' / 'Pald to' block
        for i, line in enumerate(lines):
            line_clean = line.strip()
            line_l = line_clean.lower()
            if line_l.startswith('paid to') or line_l.startswith('pald to') or line_l.startswith('pard to') or line_l.startswith('pay to') or line_l == 'paid to' or line_l == 'pald to':
                first_part = re.sub(r'^(?:paid|pald|pard|pay)\s*to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                c = clean_person_name(first_part)
                if c:
                    t.recipient_name = c
                    break
                for offset in (1, 2, 3):
                    if i + offset < len(lines):
                        next_l = lines[i + offset].strip()
                        if '@' in next_l or any(k in next_l.lower() for k in ('paid', 'pald', 'from', 'ref', 'upi', 'date', 'bank', 'amazon')):
                            continue
                        c = clean_person_name(next_l)
                        if c and len(c.replace(' ', '').replace('.', '')) > 2:
                            t.recipient_name = c
                            break
                if t.recipient_name:
                    break

        if not t.recipient_name:
            for i, line in enumerate(lines):
                if re.search(r'[a-zA-Z0-9*._-]+@(?:ybl|ibl|axl|apl|rapl|upi|paytm|okhdfcbank|oksbi)', line, re.IGNORECASE):
                    name_on_line = re.sub(r'[a-zA-Z0-9*._-]+@[a-zA-Z0-9._-]+', '', line).strip()
                    c = clean_person_name(name_on_line)
                    if c and len(c) > 2:
                        t.recipient_name = c
                        break
                    if i > 0:
                        prev_l = lines[i - 1].strip()
                        if not any(k in prev_l.lower() for k in ('paid', 'pald', 'success', 'amazon', '₹', 'rs')):
                            c = clean_person_name(prev_l)
                            if c and len(c) > 2:
                                t.recipient_name = c
                                break

        # 4. Extract Sender / Bank from 'Paid from' block
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if line_clean.lower().startswith('paid from') or line_clean.lower().startswith('from'):
                for offset in (0, 1, 2, 3, 4):
                    if i + offset < len(lines):
                        next_l = lines[i + offset].strip()
                        if any(b in next_l.lower() for b in ('bank', 'biarik', 'sbi', 'hdfc', 'icici', 'axis', 'union', 'kotak', 'pnb', 'bob', 'state bank')):
                            bank_clean = re.sub(r'[*xX\d]+$', '', next_l).strip(' -?":.,')
                            bank_clean = re.sub(r'\b(biarik)\b', 'Bank', bank_clean, flags=re.IGNORECASE)
                            t.bank_name = bank_clean.title().replace(" Of ", " of ")
                            acc_m = re.search(r'[*xX\s]+(\d{3,6})\b', next_l)
                            if acc_m:
                                t.bank_account = acc_m.group(1)
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

        # 6. Extract Date & Time
        for line in lines:
            if "date" in line.lower() or "time" in line.lower() or re.search(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)', line, re.IGNORECASE):
                d_match = re.search(r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)', line, re.IGNORECASE)
                if d_match:
                    t.transaction_date = parse_date(d_match.group(1))
                t_match = re.search(r'(\d{1,2}[:.]\d{2}\s*(?:[aApP][mM])?)', line)
                if t_match:
                    t.transaction_time = parse_time(t_match.group(1))
                if t.transaction_date:
                    break

        if not t.transaction_date:
            from utils.dates import get_current_time_in_tz
            t.transaction_date = get_current_time_in_tz().date()

        if t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name
        else:
            t.person_name = t.sender_name or t.person_name

        return t
