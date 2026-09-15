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
            "amazon reference id" in text_lower or "amazonpay" in text_lower or
            "arnizon reference" in text_lower  # common OCR misread of "Amazon"
        )

    def _extract_amazon_pay_region(self) -> list[str]:
        """
        Isolates the Amazon Pay receipt region from a full-screen Telegram chat screenshot.
        Returns only the lines belonging to the Amazon Pay receipt.
        """
        lines = self.lines
        region_start = None
        region_end = len(lines)

        # Find start: "amazon pay" or "amazonpay" header
        for i, line in enumerate(lines):
            ll = line.lower().replace(' ', '')
            if 'amazonpay' in ll:
                region_start = i
                break

        if region_start is None:
            # Fallback: look for @apl or "arnizon"
            for i, line in enumerate(lines):
                ll = line.lower()
                if '@apl' in ll or '@rapl' in ll or 'arnizon' in ll:
                    region_start = max(0, i - 5)
                    break

        if region_start is None:
            return lines  # Can't isolate, use everything

        # Find end: look for typical markers that come after the receipt
        # (the UPI logo area, followed by chat messages)
        for i in range(region_start + 1, len(lines)):
            ll = lines[i].lower().strip()
            # Lines after the receipt are Telegram chat messages
            if any(k in ll for k in self._CHAT_NOISE_KEYWORDS):
                region_end = i
                break
            # A lone "UPI" line often appears at the bottom of the receipt image
            if ll == 'upi' and i > region_start + 5:
                region_end = i + 1
                break

        return lines[region_start:region_end]

    def _normalize_line(self, line: str) -> str:
        """Normalize OCR artifacts in a line for better matching."""
        # Fix common OCR concatenation: "OPaidsuccessfully" -> "Paid successfully"
        # The leading O comes from the green checkmark icon
        normalized = re.sub(r'^O(?=Paid|paid|Payment|payment)', '', line)
        # Fix concatenated words: "Paidsuccessfully" -> "Paid successfully"
        normalized = re.sub(r'(?i)(paid|payment)(successfully|successful)', r'\1 \2', normalized)
        return normalized

    def parse(self) -> Transaction:
        t = Transaction()
        t.ocr_text = self.raw_text
        t.payment_app = "Amazon Pay"

        # Use isolated Amazon Pay region to avoid noise from surrounding chat
        region_lines = self._extract_amazon_pay_region()
        lines = self.lines  # Keep full lines for fallback searches
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
            # Fallback: check the original full text
            if any(k in text_lower for k in ('paid successfully', 'paidsuccessfully', 'paid to', 'pald to',
                                              'paid from', 'payment to', 'payment successful', 'sent to')):
                t.transaction_type = "SENT"
                t.payment_status = "SUCCESS"
            elif any(k in text_lower for k in ('received', 'money received', 'credited to')):
                t.transaction_type = "RECEIVED"
                t.payment_status = "SUCCESS"
            else:
                t.transaction_type = "SENT"

        # 2. Extract Amount from the receipt region
        # Strategy A: Find "Paid successfully" / "Payment successful" and look at nearby lines
        amount_found = False
        for idx, (orig_line, norm_line) in enumerate(normalized_region):
            norm_l = norm_line.lower()
            if any(k in norm_l for k in ('paid successfully', 'payment successful', 'received successfully',
                                          'paidsuccessfully', 'paymentsuccessful')):
                # Search the same line and the next few lines for an amount
                for offset in range(0, 4):
                    check_idx = idx + offset
                    if check_idx >= len(region_lines):
                        break
                    cand = region_lines[check_idx].strip()
                    # Skip lines that are obviously not amounts
                    if self._is_promo_or_balance_line(cand):
                        continue
                    if any(k in cand.lower() for k in self._CHAT_NOISE_KEYWORDS):
                        continue
                    cands = extract_amounts_from_line(cand)
                    valid = [c for c in cands if not self.is_invalid_amount(c, cand)]
                    if valid:
                        t.amount = valid[0]
                        amount_found = True
                        break
                if amount_found:
                    break

        # Strategy B: If no amount yet, search region lines for currency-prefixed amounts
        if not amount_found:
            for orig_line in region_lines:
                line_stripped = orig_line.strip()
                if self._is_promo_or_balance_line(line_stripped):
                    continue
                if any(k in line_stripped.lower() for k in ('upi', 'ref', 'date', 'bank', '****', '***',
                                                              'amazon reference', 'arnizon reference',
                                                              *self._CHAT_NOISE_KEYWORDS)):
                    continue
                # Skip "Paid to", "Paid from" header lines
                if re.match(r'(?i)^(?:paid|pald|pard|pay)\s*(?:to|from)', line_stripped):
                    continue
                cands = extract_amounts_from_line(line_stripped)
                valid = [c for c in cands if not self.is_invalid_amount(c, line_stripped)]
                if valid:
                    t.amount = valid[0]
                    amount_found = True
                    break

        # Strategy C: Last resort - look for standalone number lines (like "400") in the region
        if not amount_found:
            for orig_line in region_lines:
                line_stripped = orig_line.strip()
                # A standalone number line (just digits, possibly with commas)
                if re.match(r'^[\d,]+(?:\.\d{1,2})?$', line_stripped):
                    val = extract_amounts_from_line(line_stripped)
                    if val and val[0] > 0 and not self.is_invalid_amount(val[0], line_stripped):
                        t.amount = val[0]
                        break

        # 3. Extract Recipient from 'Paid to' / 'Pald to' block
        for i, line in enumerate(region_lines):
            line_clean = line.strip()
            line_l = line_clean.lower()
            if (line_l.startswith('paid to') or line_l.startswith('pald to') or
                    line_l.startswith('pard to') or line_l.startswith('pay to') or
                    line_l == 'paid to' or line_l == 'pald to'):
                first_part = re.sub(r'^(?:paid|pald|pard|pay)\s*to\s*[:.-]*\s*', '', line_clean,
                                    flags=re.IGNORECASE).strip()
                c = clean_person_name(first_part)
                if c:
                    t.recipient_name = c
                    break
                for offset in (1, 2, 3):
                    if i + offset < len(region_lines):
                        next_l = region_lines[i + offset].strip()
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
            for i, line in enumerate(region_lines):
                if re.search(
                        r'[a-zA-Z0-9*._-]+@(?:ybl|ibl|axl|apl|rapl|upi|paytm|okhdfcbank|oksbi)', line,
                        re.IGNORECASE):
                    name_on_line = re.sub(r'[a-zA-Z0-9*._-]+@[a-zA-Z0-9._-]+', '', line).strip()
                    c = clean_person_name(name_on_line)
                    if c and len(c) > 2:
                        t.recipient_name = c
                        break
                    if i > 0:
                        prev_l = region_lines[i - 1].strip()
                        if not any(k in prev_l.lower() for k in (
                                'paid', 'pald', 'success', 'amazon', 'arnizon', '\u20b9', 'rs')):
                            c = clean_person_name(prev_l)
                            if c and len(c) > 2:
                                t.recipient_name = c
                                break

        # 4. Extract Sender / Bank from 'Paid from' block
        for i, line in enumerate(region_lines):
            line_clean = line.strip()
            line_lower = line_clean.lower()
            if line_lower.startswith('paid from') or (line_lower.startswith('from') and 'from:' not in line_lower):
                for offset in (0, 1, 2, 3, 4):
                    if i + offset < len(region_lines):
                        next_l = region_lines[i + offset].strip()
                        next_lower = next_l.lower()
                        # Check for bank name (handle OCR typos like "Biank" for "Bank")
                        if any(b in next_lower for b in (
                                'bank', 'biank', 'biarik', 'sbi', 'hdfc', 'icici', 'axis', 'union',
                                'kotak', 'pnb', 'bob', 'state bank')):
                            bank_clean = re.sub(r'[*xX\d]+$', '', next_l).strip(' -?":.,' )
                            bank_clean = re.sub(r'\b(biarik|biank)\b', 'Bank', bank_clean, flags=re.IGNORECASE)
                            # Remove leading "O " from OCR green-dot artifact
                            bank_clean = re.sub(r'^O\s+', '', bank_clean)
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
        for line in region_lines:
            line_lower = line.lower()
            # Handle OCR typos: "I0" for "ID", "transaction I0" for "transaction ID"
            if ('upi transaction' in line_lower or 'transaction id' in line_lower or
                    'transaction i0' in line_lower):
                ref_m = re.search(r'\b([0-9]{12})\b', line)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break
        if not t.reference_number:
            for line in region_lines:
                ref_m = re.search(r'(?:ref(?:\s*no)?|utr)[\s:.-]*([0-9]{12})', line, re.IGNORECASE)
                if ref_m:
                    t.reference_number = ref_m.group(1)
                    break

        # 6. Extract Date & Time
        for line in region_lines:
            line_lower = line.lower()
            # Handle OCR typos: "tine" for "time"
            if ('date' in line_lower or 'time' in line_lower or 'tine' in line_lower or
                    re.search(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)',
                              line, re.IGNORECASE)):
                d_match = re.search(
                    r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)',
                    line, re.IGNORECASE)
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
