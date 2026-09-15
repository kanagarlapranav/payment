import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name, split_camel_case
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount, extract_amounts_from_line


class AmazonPayParser(GenericParser):
    """Parser specifically tuned for Amazon Pay UPI payment receipts and screenshots."""

    # Lines containing these keywords are noise from surrounding chat / UI and must be skipped
    _CHAT_NOISE_KEYWORDS = (
        'payment_tracker', 'auto-backup', 'pinned', 'connecting',
        'could not detect', 'enter manually', 'clearer screenshot',
        'message', 'admin', 'photo', 'balance:', 'amount:',
    )

    def can_parse(self) -> bool:
        text_lower = self.raw_text.lower()
        return (
            "amazon pay" in text_lower or "@apl" in text_lower or "@rapl" in text_lower or
            "amazon reference" in text_lower or "amazonpay" in text_lower or
            "arnizon reference" in text_lower or "amazon upi" in text_lower or
            "amazon" in text_lower or
            ("paid successfully" in text_lower and any(k in text_lower for k in ('upi', 'state bank', 'bank', '@ybl', '@apl', 'amazon')))
        )

    def _extract_amazon_pay_region(self) -> list[str]:
        """
        Isolates the Amazon Pay receipt region from a full-screen Telegram chat screenshot.
        Returns only the lines belonging to the Amazon Pay receipt.
        """
        lines = self.lines
        region_start = None
        region_end = len(lines)

        # 1. First look for "amazon pay" header in the first few lines
        for i in range(min(5, len(lines))):
            ll = lines[i].lower().replace(' ', '')
            if 'amazonpay' in ll or 'amazon' in ll or 'arnizon' in ll:
                region_start = i
                break

        # 2. Check if "Paid successfully" or "Paid to" is near the top
        if region_start is None:
            for i, line in enumerate(lines):
                ll = line.lower()
                if any(k in ll for k in ('paid successfully', 'paidsuccessfully', 'payment successful', 'paymentsuccessful', 'paid to', 'pald to')):
                    region_start = i
                    break

        # 3. If amazon appears later down (e.g. in "Paid from: Amazon Pay UPI"), start 6 lines before it to catch amount & recipient
        if region_start is None:
            for i, line in enumerate(lines):
                ll = line.lower().replace(' ', '')
                if 'amazonpay' in ll or 'amazon' in ll or 'arnizon' in ll:
                    region_start = max(0, i - 6)
                    break

        if region_start is None:
            for i, line in enumerate(lines):
                ll = line.lower()
                if '@apl' in ll or '@rapl' in ll:
                    region_start = max(0, i - 6)
                    break

        if region_start is None:
            return lines  # Can't isolate, use everything

        # Find end: look for typical markers that come after the receipt
        for i in range(region_start + 1, len(lines)):
            ll = lines[i].lower().strip()
            if any(k in ll for k in self._CHAT_NOISE_KEYWORDS):
                region_end = i
                break
            if ll == 'upi' and i > region_start + 4:
                region_end = i + 1
                break

        return lines[region_start:region_end]

    def _normalize_line(self, line: str) -> str:
        """Normalize OCR artifacts in a line for better matching."""
        s = line.strip()
        # Fix leading OCR green checkmark artifacts: "O Paid" -> "Paid", "O400" -> "400", "O 400" -> "400"
        s = re.sub(r'^[OoQq0]\s*(?=[A-Za-z]|\d)', '', s)
        s = re.sub(r'(?i)(paid|payment)(successfully|successful)', r'\1 \2', s)
        return s

    def parse(self) -> Transaction:
        t = Transaction()
        t.ocr_text = self.raw_text
        t.payment_app = "Amazon Pay"

        # Use isolated Amazon Pay region to avoid noise from surrounding chat
        region_lines = self._extract_amazon_pay_region()
        lines = self.lines
        text_lower = self.raw_text.lower()

        # Normalize all region lines for matching
        normalized_region = [(line, self._normalize_line(line)) for line in region_lines]

        # 1. Type Detection
        region_text_lower = '\n'.join(region_lines).lower()
        if any(k in region_text_lower for k in ('paid successfully', 'paidsuccessfully', 'paid to', 'pald to',
                                                  'paid from', 'payment to', 'payment successful',
                                                  'paymentsuccessful', 'sent to')):
            t.transaction_type = "SENT"
            t.payment_status = "SUCCESS"
        elif any(k in region_text_lower for k in ('received', 'money received', 'credited to')):
            t.transaction_type = "RECEIVED"
            t.payment_status = "SUCCESS"
        else:
            t.transaction_type = "SENT"

        # 2. Extract Amount
        amount_found = False

        # Strategy A: Check lines around "Paid successfully" / "Payment successful"
        for i, (orig_line, norm_line) in enumerate(normalized_region):
            if any(k in norm_line.lower() for k in (
                    'paid successfully', 'paidsuccessfully', 'payment successful',
                    'paymentsuccessful', 'success')):
                for offset in (0, 1, 2, 3, -1):
                    idx = i + offset
                    if 0 <= idx < len(normalized_region):
                        cand_orig, cand_norm = normalized_region[idx]
                        if self._is_promo_or_balance_line(cand_orig):
                            continue
                        if offset > 0 and re.match(r'(?i)^(?:paid|pald|pay)\s*(?:to|from)', cand_norm.strip()):
                            break
                        for text_to_try in (cand_norm, cand_orig):
                            cands = extract_amounts_from_line(text_to_try)
                            valid = [c for c in cands if not self.is_invalid_amount(c, text_to_try)]
                            if valid:
                                t.amount = valid[0]
                                amount_found = True
                                break
                    if amount_found:
                        break
            if amount_found:
                break

        # Strategy B: If no amount yet, search region lines for currency-prefixed amounts
        if not amount_found:
            for cand_orig, cand_norm in normalized_region:
                if self._is_promo_or_balance_line(cand_orig):
                    continue
                if any(k in cand_norm.lower() for k in ('upi', 'ref', 'date', 'bank', '****', '***',
                                                          'amazon reference', 'arnizon reference',
                                                          *self._CHAT_NOISE_KEYWORDS)):
                    continue
                if re.match(r'(?i)^(?:paid|pald|pay)\s*(?:to|from)', cand_norm.strip()):
                    continue
                for text_to_try in (cand_norm, cand_orig):
                    cands = extract_amounts_from_line(text_to_try)
                    valid = [c for c in cands if not self.is_invalid_amount(c, text_to_try)]
                    if valid:
                        t.amount = valid[0]
                        amount_found = True
                        break
                if amount_found:
                    break

        # Strategy C: Standalone number lines (like "400" or "O400") in the region
        if not amount_found:
            for cand_orig, cand_norm in normalized_region:
                clean_digits = re.sub(r'^[^\d]*', '', cand_norm).strip()
                if re.match(r'^[\d,]+(?:\.\d{1,2})?$', clean_digits):
                    val = extract_amounts_from_line(clean_digits)
                    if val and val[0] > 0 and not self.is_invalid_amount(val[0], clean_digits):
                        t.amount = val[0]
                        break

        # 3. Extract Recipient from 'Paid to' / 'Pald to' / 'Payment to' block
        for i, (orig_line, norm_line) in enumerate(normalized_region):
            line_clean = norm_line.strip()
            line_l = line_clean.lower()
            if any(line_l.startswith(p) for p in ('paid to', 'pald to', 'pard to', 'pay to', 'payment to', 'sent to', 'to:')):
                first_part = re.sub(r'^(?:paid|pald|pard|pay|payment|sent)\s*(?:to|to:)\s*[:.-]*\s*', '', line_clean,
                                    flags=re.IGNORECASE).strip()
                c = clean_person_name(first_part)
                if c:
                    t.recipient_name = c
                    break
                for offset in (1, 2, 3):
                    if i + offset < len(normalized_region):
                        next_orig, next_norm = normalized_region[i + offset]
                        next_l = next_norm.strip()
                        if '@' in next_l or any(k in next_l.lower() for k in (
                                'paid', 'pald', 'from', 'ref', 'upi', 'date', 'bank', 'amazon', 'arnizon')):
                            continue
                        c = clean_person_name(next_l)
                        if c and len(c.replace(' ', '').replace('.', '')) > 2:
                            t.recipient_name = c
                            break
                if t.recipient_name:
                    break

        # Fallback: search for UPI handle and extract name from nearby line
        if not t.recipient_name:
            for i, (orig_line, norm_line) in enumerate(normalized_region):
                if re.search(
                        r'[a-zA-Z0-9*._-]+@(?:ybl|ibl|axl|apl|rapl|upi|paytm|okhdfcbank|oksbi)', norm_line,
                        re.IGNORECASE):
                    name_on_line = re.sub(r'[a-zA-Z0-9*._-]+@[a-zA-Z0-9._-]+', '', norm_line).strip()
                    c = clean_person_name(name_on_line)
                    if c and len(c) > 2:
                        t.recipient_name = c
                        break
                    if i > 0:
                        prev_orig, prev_norm = normalized_region[i - 1]
                        prev_l = prev_norm.strip()
                        if not any(k in prev_l.lower() for k in (
                                'paid', 'pald', 'success', 'amazon', 'arnizon', '₹', 'rs')):
                            c = clean_person_name(prev_l)
                            if c and len(c) > 2:
                                t.recipient_name = c
                                break

        # 4. Extract Sender / Bank from 'Paid from' block
        for i, (orig_line, norm_line) in enumerate(normalized_region):
            line_clean = norm_line.strip()
            line_lower = line_clean.lower()
            if line_lower.startswith('paid from') or (line_lower.startswith('from') and 'from:' not in line_lower):
                for offset in (0, 1, 2, 3, 4):
                    if i + offset < len(normalized_region):
                        next_orig, next_norm = normalized_region[i + offset]
                        next_l = next_norm.strip()
                        next_lower = next_l.lower()
                        # Check for bank name
                        if any(b in next_lower for b in (
                                'bank', 'biank', 'biarik', 'sbi', 'hdfc', 'icici', 'axis', 'union',
                                'kotak', 'pnb', 'bob', 'state bank')):
                            bank_clean = re.sub(r'[*xX\d]+$', '', next_l).strip(' -?":.,')
                            bank_clean = re.sub(r'\b(biarik|biank)\b', 'Bank', bank_clean, flags=re.IGNORECASE)
                            bank_clean = re.sub(r'^[OoQq0]\s+', '', bank_clean)
                            t.bank_name = bank_clean.title().replace(" Of ", " of ")
                            acc_m = re.search(r'[*xX\s]+(\d{3,6})\b', next_l)
                            if acc_m:
                                t.bank_account = acc_m.group(1)
                        upi_m = re.search(
                            r'([a-zA-Z0-9*.\-_]+@(?:apl|rapl|ybl|ibl|axl|upi|paytm))', next_l,
                            re.IGNORECASE)
                        if upi_m and not t.upi_id:
                            t.upi_id = upi_m.group(1)

        # 5. Extract UPI Transaction ID (12-digit UTR)
        for orig_line, norm_line in normalized_region:
            line_lower = norm_line.lower()
            if any(k in line_lower for k in ('upi', 'tran', 'ref', 'id', 'io', 'i0', 'utr')):
                ref_m = re.search(r'\b([0-9]{12})\b', norm_line)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break

        if not t.reference_number:
            for orig_line, norm_line in normalized_region:
                ref_m = re.search(r'\b([0-9]{12})\b', norm_line)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break

        # 6. Extract Date & Time
        for orig_line, norm_line in normalized_region:
            line_lower = norm_line.lower()
            if ('date' in line_lower or 'time' in line_lower or 'tine' in line_lower or
                    re.search(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)',
                              norm_line, re.IGNORECASE)):
                d_match = re.search(
                    r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)',
                    norm_line, re.IGNORECASE)
                if d_match:
                    t.transaction_date = parse_date(d_match.group(1))
                t_match = re.search(r'(\d{1,2}[:.]\d{2}\s*(?:[aApP][mM])?)', norm_line)
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
