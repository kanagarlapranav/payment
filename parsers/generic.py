import re
from parsers.base import BasePaymentParser, clean_person_name, split_camel_case
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount, extract_amounts_from_line

class GenericParser(BasePaymentParser):
    """Fallback parser using generic keywords and layout analysis."""
    
    def can_parse(self) -> bool:
        return True

    PROMO_KEYWORDS = (
        'cashback', 'download', 'offer', 'coupon', 'reward', 'earn',
        'get up to', 'win up to', 'scratch card', 'bonus', 'promo',
        'install', 'invite', 'refer', 'onelink', 'playstore', 'appstore',
        'balance before', 'balance after', 'closing balance', 'available balance',
    )

    @staticmethod
    def _is_promo_or_balance_line(line: str) -> bool:
        """Returns True if the line is promotional/ad text or a balance line."""
        ll = line.lower()
        return any(kw in ll for kw in GenericParser.PROMO_KEYWORDS)

    @staticmethod
    def is_invalid_amount(cand_val: float, line_str: str) -> bool:
        """Determines if candidate float is an account number, phone, ref, year, or time."""
        if cand_val <= 0:
            return True
        val_int = int(cand_val)
        # Ignore 10-digit phone numbers, 11-18 digit ref/transaction numbers, or > 10 crores without words
        if val_int >= 100000000 or len(str(val_int)) in (10, 11, 12, 13, 14, 15, 16):
            return True
        # Ignore years
        if val_int in range(2023, 2035):
            return True
        # Ignore UPI ID lines with @
        if '@' in line_str:
            return True
        line_l = line_str.lower()
        # Ignore lines that are just times like "10:58 AM" or "11:00"
        if re.search(r'\b\d{1,2}:\d{2}\b', line_str) and len(line_str.strip()) < 14:
            return True
        # Ignore network speeds or battery percentages
        if any(k in line_l for k in ('kb/s', 'mb/s', 'gb/s', 'g+', '5g', '4g', 'lte', '%', 'battery')):
            return True
        # Ignore masked account numbers e.g. "****7751", "***1185", "xx7751", "A/c 7751"
        if re.search(r'[*xX]{2,}\s*' + str(val_int) + r'\b', line_str):
            return True
        if any(k in line_l for k in ('bank', 'biarik', 'a/c', 'acct', 'account', 'ending in')) and re.search(r'[-?*xX\s]+' + str(val_int) + r'\b', line_str):
            return True
        # Ignore day numbers in date strings (e.g., 15 in "September 15 at 1:41 PM") if no currency symbol present
        month_kw = r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b'
        if re.search(month_kw, line_l) and not any(c in line_str for c in ('₹', 'Rs', 'RS', 'INR', 'inr')):
            if re.search(r'\b' + str(val_int) + r'\b', line_str):
                return True
        return False


    def parse(self) -> Transaction:
        t = Transaction()
        t.ocr_text = self.raw_text
        text_lower = self.raw_text.lower()
        lines = self.lines

        # 1. Determine Type
        received_keywords = [
            'money received', 'moneyreceived', 'payment received', 'paymentreceived',
            'received successfully', 'received from', 'received at', 'receivedat',
            'credited to', 'credited', 'amount received', 'refund received'
        ]
        sent_keywords = [
            'paid successfully', 'paidsuccessfully', 'payment successful', 'paymentsuccessful',
            'paid to', 'pald to', 'paidto', 'sent to', 'sentto', 'sent successfully', 'sentsuccessfully',
            'transferred to', 'transfer to', 'debited from', 'debited', 'transferred successfully',
            'payment of', 'money sent'
        ]
        
        is_received = any(kw in text_lower for kw in received_keywords) or bool(re.search(r'\b(received|credited|deposit(?:ed)?)\b', text_lower))
        is_sent = any(kw in text_lower for kw in sent_keywords) or bool(re.search(r'\b(paid|sent|transfer(?:red)?|debited|spent)\b', text_lower))
        
        if is_received and not is_sent:
            t.transaction_type = 'RECEIVED'
        elif is_sent and not is_received:
            t.transaction_type = 'SENT'
        elif is_received and is_sent:
            if any(kw in text_lower for kw in ('received from', 'money received', 'payment received', 'credited to')):
                t.transaction_type = 'RECEIVED'
            elif any(kw in text_lower for kw in ('paid to', 'pald to', 'sent to', 'transferred to', 'debited from', 'paid successfully', 'money sent')):
                t.transaction_type = 'SENT'
            else:
                first_few = " ".join(lines[:5]).lower()
                if any(kw in first_few for kw in received_keywords):
                    t.transaction_type = 'RECEIVED'
                else:
                    t.transaction_type = 'SENT'
        else:
            t.transaction_type = 'SENT' if any(w in text_lower for w in ('to', 'debit', 'spent')) else ('RECEIVED' if 'credit' in text_lower else '')

        # 2. Extract Amount
        word_amount = 0.0
        for line in lines:
            if any(w in line.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'only')) and any(w in line.lower() for w in ('rupees', 'rs', 'inr', 'only')):
                w_amt = parse_amount(line)
                if w_amt > 0:
                    word_amount = w_amt
                    break
        if word_amount == 0.0:
            words_match = re.search(r'(?:rupees|rs\.?|inr)\s+([a-zA-Z\s]+?)\s+only', self.raw_text, re.IGNORECASE)
            if words_match:
                word_amount = parse_amount(words_match.group(0))

        header_keywords = (
            'money received', 'payment received', 'paid successfully',
            'sent successfully', 'transferred successfully', 'payment to',
            'paid to', 'pald to', 'received from', 'transfer to', 'money sent',
        )
        header_amount = 0.0
        for i, line in enumerate(lines):
            line_l = line.lower().strip()
            if any(p in line_l for p in ('initiated by', 'transferred from', 'received by', 'narration', 'upi/dr', 'upi/cr', 'description')):
                continue
            is_header = any(h in line_l for h in header_keywords)
            if not is_header and re.search(r'\b(?:paid|received|sent|transferred)\b', line_l):
                is_header = True
            if is_header:
                for offset in (-2, -1, 0, 1, 2, 3):
                    idx = i + offset
                    if 0 <= idx < len(lines):
                        cand = lines[idx].strip()
                        if self._is_promo_or_balance_line(cand):
                            continue
                        if offset > 0 and any(w in cand.lower() for w in ('account', 'instrument', 'initiated', 'transferred from', 'seconds', 'narration', 'bank', 'biarik', '****', '***')):
                            continue
                        cands = extract_amounts_from_line(cand)
                        for c in cands:
                            if not self.is_invalid_amount(c, cand):
                                header_amount = c
                                break
                        if header_amount > 0:
                            break
                if header_amount > 0:
                    break

        currency_matches = []
        for line in lines:
            if self._is_promo_or_balance_line(line) or any(w in line.lower() for w in ('****', '***', 'xx', 'ending in')):
                continue
            if re.search(r'[₹$€£¥?*]|Rs\.?|INR|[RrFf](?=\d)', line):
                cands = extract_amounts_from_line(line)
                for c in cands:
                    if not self.is_invalid_amount(c, line):
                        currency_matches.append(c)

        labeled_matches = []
        for i, line in enumerate(lines):
            if re.search(r'\b(amount|total)\b', line, re.IGNORECASE):
                for offset in (0, 1):
                    if i + offset < len(lines):
                        cands = extract_amounts_from_line(lines[i + offset])
                        for c in cands:
                            if not self.is_invalid_amount(c, lines[i + offset]):
                                labeled_matches.append(c)

        if word_amount > 0:
            t.amount = word_amount
        elif header_amount > 0:
            t.amount = header_amount
        elif currency_matches:
            t.amount = currency_matches[0]
        elif labeled_matches:
            t.amount = labeled_matches[0]
        else:
            all_cands = []
            for line in lines:
                if not self._is_promo_or_balance_line(line) and not any(w in line.lower() for w in ('bank', 'biarik', '****', '***', 'xx')):
                    for c in extract_amounts_from_line(line):
                        if not self.is_invalid_amount(c, line):
                            all_cands.append(c)
            t.amount = all_cands[0] if all_cands else 0.0

        # 3. Extract Reference / UTR Number
        ref_patterns = [
            r'(?:upi\s*ref(?:\s*no)?|ref(?:\s*no)?|reference(?:\s*no)?|utr|txn(?:\s*id)?|transaction\s*id)[\s:.-]*([0-9\s]{12,18})',
            r'\b(\d{4}\s\d{4}\s\d{4})\b',
            r'\b([0-9]{12})\b',
            r'(?:upi\s*ref(?:\s*no)?|ref(?:\s*no)?|reference(?:\s*no)?|utr|txn(?:\s*id)?|transaction\s*id)[\s:.-]*([a-zA-Z0-9]{10,24})'
        ]
        for pat in ref_patterns:
            ref_match = re.search(pat, self.raw_text, re.IGNORECASE)
            if ref_match:
                ref_val = ref_match.group(1).strip()
                ref_val = re.sub(r'(copy|share|view|history)$', '', ref_val, flags=re.IGNORECASE).strip()
                ref_digits = re.sub(r'\s+', '', ref_val)
                if len(ref_digits) >= 12 and ref_digits[:12].isdigit():
                    t.reference_number = ref_digits[:12]
                    break
                elif len(ref_val) >= 9:
                    t.reference_number = ref_digits.upper()
                    break

        # 4. Extract Date and Time
        if 'yesterday' in self.raw_text.lower():
            t.transaction_date = parse_date('yesterday')
        elif 'today' in self.raw_text.lower():
            t.transaction_date = parse_date('today')
        else:
            for line in lines:
                if re.search(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)', line, re.IGNORECASE) or 'date' in line.lower():
                    d_match = re.search(r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)', line, re.IGNORECASE)
                    if d_match:
                        t.transaction_date = parse_date(d_match.group(1))
                    t_match = re.search(r'(\d{1,2}[:.]\d{2}\s*(?:[aApP][mM])?)', line)
                    if t_match:
                        t.transaction_time = parse_time(t_match.group(1))
                    if t.transaction_date:
                        break

        # 5. Extract Names
        for i, line in enumerate(lines):
            line_clean = line.strip()
            
            sent_to_match = re.search(r'(?:paid|pald|pard|sent|transfer(?:red)?|payment)\s+to\s+([a-zA-Z\s]+?)(?=\s+paid|\s+sent|\s+received|\s+via|\s+for|\s+on|\s+using|\s+at|\s+\d|$|\n)', line_clean, re.IGNORECASE)
            if not sent_to_match:
                sent_to_match = re.search(r'(?:paid|pald|pard|sent|transfer(?:red)?|payment)\s+to\s+([a-zA-Z\s]+)', line_clean, re.IGNORECASE)
            if sent_to_match:
                val = clean_person_name(sent_to_match.group(1))
                if val:
                    t.recipient_name = val
                    
            recv_from_match = re.search(r'(?:received|money\s+received).*?\bfrom\s+([a-zA-Z\s]+?)(?=\s+via|\s+for|\s+on|\s+using|\s+at|$|\n)', line_clean, re.IGNORECASE)
            if recv_from_match:
                val = clean_person_name(recv_from_match.group(1))
                if val:
                    t.sender_name = val

            if not t.sender_name:
                conv_from = re.search(r'\bfrom\s+([a-zA-Z\s]+?)(?=\s+via|\s+for|\s+on|\s+using|\s+at|$|\n)', line_clean, re.IGNORECASE)
                if conv_from:
                    val = clean_person_name(conv_from.group(1))
                    if val and "bank" not in val.lower() and "phonepe" not in val.lower() and "paytm" not in val.lower():
                        t.sender_name = val

            if not t.recipient_name:
                conv_to = re.search(r'\bto\s+([a-zA-Z\s]+?)(?=\s+via|\s+for|\s+on|\s+using|\s+at|$|\n)', line_clean, re.IGNORECASE)
                if conv_to:
                    val = clean_person_name(conv_to.group(1))
                    if val and "bank" not in val.lower() and "phonepe" not in val.lower() and "paytm" not in val.lower():
                        t.recipient_name = val

            if re.match(r'^from\b', line_clean, re.IGNORECASE):
                val = re.sub(r'^from\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                name_parts = [val] if val else []
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|paid|received|\d)\b', next_line, re.IGNORECASE) and '@' not in next_line:
                        name_parts.append(next_line)
                val = clean_person_name(' '.join(name_parts))
                if val and not t.sender_name:
                    t.sender_name = val

            elif re.match(r'^to\b', line_clean, re.IGNORECASE):
                val = re.sub(r'^to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                name_parts = [val] if val else []
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if not re.match(r'^(?:upi|to|from|bank|ref|a/c|account|paid|received|\d)\b', next_line, re.IGNORECASE) and '@' not in next_line:
                        name_parts.append(next_line)
                val = clean_person_name(' '.join(name_parts))
                if val and not t.recipient_name:
                    t.recipient_name = val

        # 6. Extract Bank and Account Information
        for i, line in enumerate(lines):
            line_clean = line.strip()
            if not t.bank_account:
                acc_match = re.search(r'(?:account|acct|a/c)\s*(?:number|no)?[\s:.-]*[xX*]*([0-9]{3,6})\b', line_clean, re.IGNORECASE)
                if acc_match:
                    t.bank_account = acc_match.group(1)
                elif re.search(r'[-?*xX\s]+([0-9]{4})[a-zA-Z]*$', line_clean):
                    t.bank_account = re.search(r'[-?*xX\s]+([0-9]{4})[a-zA-Z]*$', line_clean).group(1)
                elif "bank" in line_clean.lower() and i + 1 < len(lines):
                    next_acc = re.search(r'^([0-9]{4})\b', lines[i + 1].strip())
                    if next_acc:
                        t.bank_account = next_acc.group(1)

            if not t.bank_name:
                if re.search(r'\b(sbi|state bank of india)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "State Bank of India"
                elif re.search(r'\b(hdfc(?:\s*bank)?)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "HDFC Bank"
                elif re.search(r'\b(icici(?:\s*bank)?)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "ICICI Bank"
                elif re.search(r'\b(axis(?:\s*bank)?)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "Axis Bank"
                elif re.search(r'\b(union\s*bank(?:\s*of\s*india)?)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "Union Bank of India"
                elif re.search(r'\b(punjab\s*national\s*bank|pnb)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "Punjab National Bank"
                elif re.search(r'\b(bank\s*of\s*baroda|bob)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "Bank of Baroda"
                elif re.search(r'\b(kotak(?:\s*mahindra)?(?:\s*bank)?)\b', line_clean, re.IGNORECASE):
                    t.bank_name = "Kotak Mahindra Bank"

            # Narration
            narr_person = re.search(r'UPI/(?:DR|CR)/[0-9a-zA-Z*xX]+/([^/]+)/', line_clean, re.IGNORECASE)
            if narr_person:
                val = clean_person_name(narr_person.group(1))
                if val:
                    t.person_name = val
                    if 'DR' in line_clean.upper():
                        t.recipient_name = val
                        t.transaction_type = 'SENT'
                    elif 'CR' in line_clean.upper():
                        t.sender_name = val
                        t.transaction_type = 'RECEIVED'

        if t.transaction_type == 'SENT':
            t.person_name = t.recipient_name or t.person_name
        else:
            t.person_name = t.sender_name or t.person_name

        if not t.transaction_date:
            from utils.dates import get_current_time_in_tz
            t.transaction_date = get_current_time_in_tz().date()

        t.payment_status = "SUCCESS" if t.transaction_type else "UNKNOWN"
        t.payment_app = "Generic"
        
        return t
