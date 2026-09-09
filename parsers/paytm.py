import re
from parsers.generic import GenericParser
from parsers.base import split_camel_case, clean_person_name
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount, extract_amounts_from_line


class PaytmParser(GenericParser):
    """Parser specifically tuned for Paytm screenshots and share receipts."""
    
    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        return (
            "paytm" in text_lower or "@paytm" in text_lower or "@ptyes" in text_lower or
            "@ptaxis" in text_lower or "@pthdfc" in text_lower or "@ptsbi" in text_lower or
            "money sent" in text_lower or "paid successfully" in text_lower and "ref no:" in text_lower
        )

    def parse(self) -> Transaction:
        # Start with generic extraction as baseline
        t = super().parse()
        t.payment_app = "Paytm"
        
        text_lower = self.raw_text.lower()
        lines = self.lines

        # 1. Determine Type
        is_sent = any(k in text_lower for k in (
            "paid successfully", "paidsuccessfully", "money sent", "moneysent",
            "sent successfully", "sentsuccessfully", "transferred successfully"
        ))
        is_received = any(k in text_lower for k in (
            "money received", "moneyreceived", "payment received", "paymentreceived",
            "received successfully", "received at", "receivedat"
        ))

        if is_sent and not is_received:
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        elif is_received and not is_sent:
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"

        # 2. Extract Amount with Word & Numeric Priority
        # First priority: Words e.g. "Three Thousand Five Hundred Rupees", "Rupees Thirty Thousand Seven Hundred Only"
        for line in lines:
            if any(w in line.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'only')) and any(w in line.lower() for w in ('rupees', 'rs', 'inr', 'only')):
                w_val = parse_amount(line)
                if w_val > 0:
                    t.amount = w_val
                    break

        # Second priority: Main amount line near header / Paid Successfully / Money Received
        if not t.amount or t.amount <= 0:
            for i, line in enumerate(lines):
                line_l = line.lower().strip()
                # If line has currency symbol or pure formatted number
                if re.search(r'[₹$€£?*]|Rs\.?|INR|[RrFf](?=\d)', line) or re.search(r'^\d{1,3}(?:[,\.]\d{2,3})+(?:\.\d{1,2})?$', line.strip()):
                    # Avoid lines with balance, ref, date, phone
                    if not any(k in line_l for k in ('ref', 'upi', 'bank', 'balance', 'closing', 'avail', 'sep', 'aug', 'oct', 'nov', 'dec', 'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'am', 'pm')):
                        cands = extract_amounts_from_line(line)
                        valid = [c for c in cands if c not in (2024, 2025, 2026, 2027) and c > 0]
                        if valid:
                            t.amount = valid[0]
                            break

        # 3. Extract Names (Sender vs Recipient)
        # Scan Paytm layout
        top_name_candidate = None
        for i, line in enumerate(lines[:6]):
            line_l = line.lower().strip()
            # Skip app headers, status bar, and amount lines
            if any(k in line_l for k in ('paytm', 'money', 'received', 'paid', 'sent', 'amount', 'success', 'http', 'download', '9346', 'union', 'bank')):
                continue
            if '@' in line or re.search(r'\d', line) or '₹' in line:
                continue
            cleaned = clean_person_name(line)
            if cleaned and len(cleaned) > 2:
                top_name_candidate = cleaned
                break

        # Extract From block
        from_name = None
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if re.match(r'^from\b', line_clean, re.IGNORECASE):
                first_part = re.sub(r'^from\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                name_parts = [first_part] if first_part else []
                if i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|union|state|hdfc|icici|axis|9\s*sep|\d)\b', next_l, re.IGNORECASE) and '@' not in next_l:
                        name_parts.append(next_l)
                from_name = clean_person_name(' '.join(name_parts))
                if from_name:
                    break

        # Extract To block
        to_name = None
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if re.match(r'^to\b', line_clean, re.IGNORECASE):
                first_part = re.sub(r'^to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                name_parts = [first_part] if first_part else []
                if i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|union|state|hdfc|icici|axis|\d)\b', next_l, re.IGNORECASE) and '@' not in next_l:
                        name_parts.append(next_l)
                to_name = clean_person_name(' '.join(name_parts))
                if to_name:
                    break

        # Map according to Paytm Transaction Type:
        if t.transaction_type == "SENT":
            # In SENT: Top card has the recipient (person you paid)!
            t.recipient_name = to_name or top_name_candidate or t.recipient_name
            t.sender_name = from_name or t.sender_name
            t.person_name = t.recipient_name or t.person_name
        else: # RECEIVED
            # In RECEIVED: From has the sender (person who paid you)!
            t.sender_name = from_name or top_name_candidate or t.sender_name
            t.recipient_name = to_name or t.recipient_name
            t.person_name = t.sender_name or t.person_name

        # 4. Extract Reference Number (Paytm uses 'UPI Ref No: 123456789012' or 'Ref No: 6252 4624 3251')
        for line in lines:
            line_l = line.lower()
            ref_m = re.search(r'(?:upi\s*ref(?:\s*no)?|ref(?:\s*no)?|reference(?:\s*no)?)[\s:.-]*([0-9\s]{12,18})', line_l)
            if ref_m:
                digits = re.sub(r'\s+', '', ref_m.group(1))
                if len(digits) >= 12 and digits[:12].isdigit():
                    t.reference_number = digits[:12]
                    break

        # 5. Extract UPI ID
        for line in lines:
            upi_m = re.search(r'([a-zA-Z0-9*.\-_]+@(?:paytm|ptyes|ptaxis|pthdfc|ptsbi|upi|ybl|ibl|axl|okhdfcbank|okaxis|okicici|oksbi))', line, re.IGNORECASE)
            if upi_m:
                t.upi_id = upi_m.group(1)
                break

        # 6. Bank & Account
        for i, line in enumerate(lines):
            line_l = line.lower()
            if "bank" in line_l and not t.bank_name:
                bank_part = line.strip()
                acc_match = re.search(r'[-–\s]+([0-9]{4})[a-zA-Z]*$', bank_part)
                if acc_match:
                    t.bank_account = acc_match.group(1)
                    bank_part = bank_part[:acc_match.start()]
                elif i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    next_acc = re.search(r'^([0-9]{4})\b', next_line)
                    if next_acc:
                        t.bank_account = next_acc.group(1)
                cleaned_bank = split_camel_case(bank_part).strip(' -–:.,')
                t.bank_name = cleaned_bank.title()

        return t
