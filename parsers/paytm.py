import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name, split_camel_case
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount, extract_amounts_from_line

class PaytmParser(GenericParser):
    """Parser specifically tuned for Paytm receipt layout."""
    
    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        return (
            "paytm" in text_lower or
            "upi ref no:" in text_lower or
            "upirefno" in text_lower or
            "p.paytm.me" in text_lower or
            "paytm payments bank" in text_lower or
            "@paytm" in text_lower or
            "@ptyes" in text_lower or
            "@ptaxis" in text_lower
        )
        
    def parse(self) -> Transaction:
        t = Transaction()
        t.ocr_text = self.raw_text
        t.payment_app = "Paytm"
        text_lower = self.raw_text.lower()
        lines = self.lines

        # 1. Determine Type
        if "money received" in text_lower or "received successfully" in text_lower or "moneyreceived" in text_lower or "received from" in text_lower:
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"
        elif "paid successfully" in text_lower or "payment successful" in text_lower or "paid to" in text_lower or "sent to" in text_lower or "money sent" in text_lower or "paidsuccessfully" in text_lower:
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        else:
            t.transaction_type = "SENT" if any(w in text_lower for w in ('paid', 'sent', 'to')) else "RECEIVED"

        # 2. Extract Amount
        for line in lines:
            if any(w in line.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'only')) and any(w in line.lower() for w in ('rupees', 'rs', 'inr', 'only')):
                w_amt = parse_amount(line)
                if w_amt > 0:
                    t.amount = w_amt
                    break
        
        if t.amount <= 0:
            for i, line in enumerate(lines):
                if re.match(r'^(?:amount|total\s*amount)\b', line.strip(), re.IGNORECASE):
                    for offset in (0, 1):
                        if i + offset < len(lines):
                            cands = extract_amounts_from_line(lines[i + offset])
                            valid = [c for c in cands if not self.is_invalid_amount(c, lines[i + offset])]
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

        # 3. Extract Date and Time
        for line in lines:
            if re.search(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)', line, re.IGNORECASE) or 'received at' in line.lower() or 'paid at' in line.lower() or 'receivedat' in line.lower():
                d_m = re.search(r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)', line, re.IGNORECASE)
                if d_m:
                    t.transaction_date = parse_date(d_m.group(1))
                t_m = re.search(r'(\d{1,2}[:.]\d{2}\s*(?:[aApP][mM])?)', line)
                if t_m:
                    t.transaction_time = parse_time(t_m.group(1))
                if t.transaction_date:
                    break

        # 4. Extract Names
        def _extract_name_near_upi(line_text):
            stripped = re.sub(r'\s+(?:on|via|using)\s+(?:phonepe|phone\s*pe|paytm|gpay|google\s*pay|bhim|bank|upi).*$', '', line_text, flags=re.IGNORECASE)
            stripped = re.sub(r'\s*[a-zA-Z0-9*._-]+@[a-zA-Z0-9._-]+\b', '', stripped).strip()
            stripped = re.sub(r'\b\d{10,}\b', '', stripped).strip()
            stripped = re.sub(r'[₹$€£¥?*]\s*[\d,]+', '', stripped).strip()
            stripped = stripped.strip(' -?":.,')
            if stripped:
                return clean_person_name(stripped)
            return ""

        top_name_candidate = None
        upi_line_idx = -1

        for i, line in enumerate(lines[:12]):
            if re.search(r'[a-zA-Z0-9*._-]+@(?:paytm|ptyes|ptaxis|pthdfc|ptsbi|upi|ybl|ibl|axl|okhdfcbank|okaxis|okicici|oksbi)', line, re.IGNORECASE):
                upi_line_idx = i
                break

        if upi_line_idx >= 0:
            name_from_upi_line = _extract_name_near_upi(lines[upi_line_idx])
            if name_from_upi_line and len(name_from_upi_line) > 2 and 'phone' not in name_from_upi_line.lower():
                top_name_candidate = name_from_upi_line
            if not top_name_candidate and upi_line_idx > 0:
                prev_line = lines[upi_line_idx - 1].strip()
                prev_l = prev_line.lower()
                if not any(k in prev_l for k in ('paytm', 'money', 'received', 'paid', 'sent', 'success', 'http', 'download', '₹', 'balance')):
                    if '@' not in prev_line and '₹' not in prev_line:
                        cleaned = clean_person_name(prev_line)
                        if cleaned and len(cleaned) > 2:
                            top_name_candidate = cleaned

        if not top_name_candidate:
            for i, line in enumerate(lines[:8]):
                line_l = line.lower().strip()
                if any(k in line_l for k in ('paytm', 'money', 'received', 'paid', 'sent', 'amount', 'success', 'http', 'download', 'union', 'bank', 'balance', 'thousand', 'hundred', 'rupees')):
                    continue
                if '@' in line:
                    name_part = _extract_name_near_upi(line)
                    if name_part and len(name_part) > 2 and 'phone' not in name_part.lower():
                        top_name_candidate = name_part
                        break
                    continue
                if re.match(r'^[\d\s,.:+%-]+$', line.strip()):
                    continue
                cleaned = clean_person_name(line)
                if cleaned and len(cleaned) > 2 and 'phone' not in cleaned.lower():
                    top_name_candidate = cleaned
                    break

        from_name = None
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if re.match(r'^from\b', line_clean, re.IGNORECASE):
                first_part = re.sub(r'^from\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                first_part = re.sub(r'\s*[a-zA-Z0-9*._-]+@[a-zA-Z0-9._-]+\b', '', first_part).strip()
                name_parts = [first_part] if first_part else []
                if i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|\d)', next_l, re.IGNORECASE) and '@' not in next_l:
                        if not re.match(r'^(?:union|state|hdfc|icici|axis|punjab|kotak|bob|canara|indian)\b', next_l, re.IGNORECASE):
                            name_parts.append(next_l)
                from_name = clean_person_name(' '.join(name_parts))
                if from_name:
                    break

        to_name = None
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if re.match(r'^to\b', line_clean, re.IGNORECASE):
                first_part = re.sub(r'^to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                first_part = re.sub(r'\s*[a-zA-Z0-9*._-]+@[a-zA-Z0-9._-]+\b', '', first_part).strip()
                name_parts = [first_part] if first_part else []
                if i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|\d)', next_l, re.IGNORECASE) and '@' not in next_l:
                        if not re.match(r'^(?:union|state|hdfc|icici|axis|punjab|kotak|bob|canara|indian)\b', next_l, re.IGNORECASE):
                            name_parts.append(next_l)
                to_name = clean_person_name(' '.join(name_parts))
                if to_name:
                    break

        if t.transaction_type == "SENT":
            t.recipient_name = to_name or top_name_candidate or t.recipient_name
            t.sender_name = from_name or t.sender_name
            t.person_name = t.recipient_name or t.person_name
        else:
            t.sender_name = from_name or top_name_candidate or t.sender_name
            t.recipient_name = to_name or t.recipient_name
            t.person_name = t.sender_name or t.person_name

        # 5. Extract Reference Number
        for line in lines:
            ref_m = re.search(r'(?:upi\s*ref(?:\s*no)?|upirefno|ref(?:\s*no)?|reference(?:\s*no)?)[\s:.-]*([0-9\s]{12,18})', line, re.IGNORECASE)
            if ref_m:
                digits = re.sub(r'\s+', '', ref_m.group(1))
                if len(digits) >= 12 and digits[:12].isdigit():
                    t.reference_number = digits[:12]
                    break
        if not t.reference_number:
            for line in lines:
                ref_m = re.search(r'\b([0-9]{12})\b', line)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break

        # 6. Extract UPI ID
        for line in lines:
            upi_m = re.search(r'([a-zA-Z0-9*.\-_]+@(?:paytm|ptyes|ptaxis|pthdfc|ptsbi|upi|ybl|ibl|axl|okhdfcbank|okaxis|okicici|oksbi))', line, re.IGNORECASE)
            if upi_m:
                t.upi_id = upi_m.group(1)
                break

        # 7. Bank & Account
        for i, line in enumerate(lines):
            line_l = line.lower()
            if "bank" in line_l and not t.bank_name:
                bank_part = line.strip()
                acc_match = re.search(r'[-?*\"\s]+([0-9]{4})[a-zA-Z]*$', bank_part)
                if acc_match:
                    t.bank_account = acc_match.group(1)
                    bank_part = bank_part[:acc_match.start()]
                elif i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    next_acc = re.search(r'^([0-9]{4})\b', next_line)
                    if next_acc:
                        t.bank_account = next_acc.group(1)
                cleaned_bank = split_camel_case(bank_part).strip(' -?":.,')
                cleaned_bank = cleaned_bank.title().replace(" Of ", " of ")
                t.bank_name = cleaned_bank

        if not t.transaction_date:
            from utils.dates import get_current_time_in_tz
            t.transaction_date = get_current_time_in_tz().date()

        return t
