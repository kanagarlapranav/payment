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
        # Paytm layout: The recipient/sender name appears as the prominent name
        # at the top of the payment card, often on the same line as or just above
        # their UPI ID (e.g. "M S Prashanth Kumar 7036046070@upi").

        # Helper: strip UPI IDs and phone-like digits from a line to isolate the name part
        def _extract_name_near_upi(line_text):
            """Strips UPI IDs and trailing digits/noise from a line, returns cleaned name or empty string."""
            # Remove UPI IDs like user@upi, user@paytm etc.
            stripped = re.sub(r'\s*[a-zA-Z0-9*._-]+@[a-zA-Z]+\b', '', line_text).strip()
            # Remove standalone phone numbers (10-digit)
            stripped = re.sub(r'\b\d{10,}\b', '', stripped).strip()
            # Remove currency symbols and amounts
            stripped = re.sub(r'[₹$€£]\s*[\d,]+', '', stripped).strip()
            stripped = stripped.strip(' -–:.,')
            if stripped:
                return clean_person_name(stripped)
            return ""

        # Strategy A: Scan the card header area (top ~10 lines) for the prominent name
        # In Paytm screenshots, the name appears above or alongside the UPI ID
        top_name_candidate = None
        upi_line_idx = -1

        # First, find the UPI ID line to anchor our search
        for i, line in enumerate(lines[:12]):
            if re.search(r'[a-zA-Z0-9*._-]+@(?:paytm|ptyes|ptaxis|pthdfc|ptsbi|upi|ybl|ibl|axl|okhdfcbank|okaxis|okicici|oksbi)', line, re.IGNORECASE):
                upi_line_idx = i
                break

        if upi_line_idx >= 0:
            # Check if the name is on the SAME line as the UPI ID (OCR merged them)
            name_from_upi_line = _extract_name_near_upi(lines[upi_line_idx])
            if name_from_upi_line and len(name_from_upi_line) > 2:
                top_name_candidate = name_from_upi_line
            # Check the line ABOVE the UPI ID (separate lines)
            if not top_name_candidate and upi_line_idx > 0:
                prev_line = lines[upi_line_idx - 1].strip()
                prev_l = prev_line.lower()
                if not any(k in prev_l for k in ('paytm', 'money', 'received', 'paid', 'sent', 'success', 'http', 'download', '₹', 'balance')):
                    if '@' not in prev_line and '₹' not in prev_line:
                        cleaned = clean_person_name(prev_line)
                        if cleaned and len(cleaned) > 2:
                            top_name_candidate = cleaned

        # Fallback: scan top lines for any standalone name (no digits, no @, no app keywords)
        if not top_name_candidate:
            for i, line in enumerate(lines[:8]):
                line_l = line.lower().strip()
                if any(k in line_l for k in ('paytm', 'money', 'received', 'paid', 'sent', 'amount', 'success', 'http', 'download', 'union', 'bank', 'balance', 'thousand', 'hundred', 'rupees')):
                    continue
                if '₹' in line:
                    continue
                # If line contains @, try to extract name portion
                if '@' in line:
                    name_part = _extract_name_near_upi(line)
                    if name_part and len(name_part) > 2:
                        top_name_candidate = name_part
                        break
                    continue
                # Skip pure digit lines
                if re.match(r'^[\d\s,.:+%-]+$', line.strip()):
                    continue
                cleaned = clean_person_name(line)
                if cleaned and len(cleaned) > 2:
                    top_name_candidate = cleaned
                    break

        # Strategy B: Extract "From" block name
        from_name = None
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if re.match(r'^from\b', line_clean, re.IGNORECASE):
                first_part = re.sub(r'^from\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                # Remove any UPI ID from the "From" line itself
                first_part = re.sub(r'\s*[a-zA-Z0-9*._-]+@[a-zA-Z]+\b', '', first_part).strip()
                name_parts = [first_part] if first_part else []
                # Look at the next line for the name (common Paytm layout: "From\nKanagarla Pranav")
                if i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|\d)', next_l, re.IGNORECASE) and '@' not in next_l:
                        # Don't skip lines with bank names here — they are separate from the person name
                        if not re.match(r'^(?:union|state|hdfc|icici|axis|punjab|kotak|bob|canara|indian)\b', next_l, re.IGNORECASE):
                            name_parts.append(next_l)
                from_name = clean_person_name(' '.join(name_parts))
                if from_name:
                    break

        # Strategy C: Extract "To" block name
        to_name = None
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if re.match(r'^to\b', line_clean, re.IGNORECASE):
                first_part = re.sub(r'^to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                first_part = re.sub(r'\s*[a-zA-Z0-9*._-]+@[a-zA-Z]+\b', '', first_part).strip()
                name_parts = [first_part] if first_part else []
                if i + 1 < len(lines):
                    next_l = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|\d)', next_l, re.IGNORECASE) and '@' not in next_l:
                        if not re.match(r'^(?:union|state|hdfc|icici|axis|punjab|kotak|bob|canara|indian)\b', next_l, re.IGNORECASE):
                            name_parts.append(next_l)
                to_name = clean_person_name(' '.join(name_parts))
                if to_name:
                    break

        # Map names according to Paytm Transaction Type:
        if t.transaction_type == "SENT":
            # In SENT: Top card has the recipient (person you paid)
            t.recipient_name = to_name or top_name_candidate or t.recipient_name
            t.sender_name = from_name or t.sender_name
            t.person_name = t.recipient_name or t.person_name
        else: # RECEIVED
            # In RECEIVED: From has the sender (person who paid you)
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
