import re
from parsers.base import BasePaymentParser, clean_person_name, split_camel_case
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount

class GenericParser(BasePaymentParser):
    """Fallback parser using generic keywords."""
    
    def can_parse(self) -> bool:
        # Always return True as this is the fallback
        return True

    def parse(self) -> Transaction:
        t = Transaction()
        t.ocr_text = self.raw_text
        text_lower = self.raw_text.lower()
        
        # 1. Determine Type
        # Strong received patterns
        received_keywords = [
            'money received', 'moneyreceived', 'payment received', 'paymentreceived',
            'received successfully', 'received from', 'received at', 'receivedat',
            'credited to', 'credited'
        ]
        # Strong sent patterns
        sent_keywords = [
            'paid successfully', 'paidsuccessfully', 'payment successful', 'paymentsuccessful',
            'paid to', 'paidto', 'sent to', 'sentto', 'sent successfully', 'sentsuccessfully',
            'transferred to', 'transfer to', 'debited from', 'debited'
        ]
        
        is_received = any(kw in text_lower for kw in received_keywords)
        is_sent = any(kw in text_lower for kw in sent_keywords)
        
        if is_received and not is_sent:
            t.transaction_type = 'RECEIVED'
        elif is_sent and not is_received:
            t.transaction_type = 'SENT'
        elif is_received and is_sent:
            # If both, check if "received" is in the main heading / top lines
            first_few = " ".join(self.lines[:5]).lower()
            if any(kw in first_few for kw in received_keywords):
                t.transaction_type = 'RECEIVED'
            else:
                t.transaction_type = 'SENT'
        else:
            # Weaker fallback check
            if 'received' in text_lower or 'credit' in text_lower:
                t.transaction_type = 'RECEIVED'
            elif 'paid' in text_lower or 'sent' in text_lower or 'debit' in text_lower:
                t.transaction_type = 'SENT'

        # 2. Extract Amount
        # Strategy A: Look for currency symbols (₹, $, €, £, Rs, INR, or R prefix like R30,700)
        amount_matches = []
        for match in re.finditer(r'(?:[₹$€£]|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)', self.raw_text, re.IGNORECASE):
            amt = parse_amount(match.group(1))
            if amt > 0:
                amount_matches.append(amt)
                
        for match in re.finditer(r'\b[Rr]([\d,]{3,}(?:\.\d{1,2})?)\b', self.raw_text):
            amt = parse_amount(match.group(1))
            if amt > 0:
                amount_matches.append(amt)

        # Strategy B: Look after "Amount", "Total", "Paid", "Received"
        if not amount_matches:
            for i, line in enumerate(self.lines):
                if re.search(r'\b(amount|total)\b', line, re.IGNORECASE):
                    # Check same line or next line
                    amt = parse_amount(line)
                    if amt > 0:
                        amount_matches.append(amt)
                    elif i + 1 < len(self.lines):
                        amt = parse_amount(self.lines[i + 1])
                        if amt > 0:
                            amount_matches.append(amt)

        # Strategy C: Look for numbers with commas (e.g. 30,700 or 1,50,000)
        if not amount_matches:
            for match in re.finditer(r'\b(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?)\b', self.raw_text):
                amt = parse_amount(match.group(1))
                if amt > 0:
                    amount_matches.append(amt)

        # Strategy D: Look for standalone numbers in conversational text (e.g. 5000, 500, 25000)
        if not amount_matches:
            for match in re.finditer(r'\b(\d{2,7}(?:\.\d{1,2})?)\b', self.raw_text):
                val = match.group(1)
                if val not in ('2023', '2024', '2025', '2026', '2027', '2028', '2029', '2030'):
                    amt = parse_amount(val)
                    if amt > 0:
                        amount_matches.append(amt)

        # Strategy E: Look for words like 'Rupees Six Thousand Two Hundred Only'
        if not amount_matches:
            for line in self.lines:
                if any(w in line.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'rupees')):
                    amt = parse_amount(line)
                    if amt > 0:
                        amount_matches.append(amt)

        if amount_matches:
            t.amount = max(amount_matches)
            
        # 3. Extract Reference / UTR Number
        # Common patterns: UPIRefNo:661385614715Copy, UTR: 123456789012, Ref No: 6132 2762 5054
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
                # Remove trailing words like 'Copy', 'Share', 'View'
                ref_val = re.sub(r'(copy|share|view|history)$', '', ref_val, flags=re.IGNORECASE).strip()
                ref_digits = re.sub(r'\s+', '', ref_val)
                if len(ref_digits) >= 12 and ref_digits[:12].isdigit():
                    t.reference_number = ref_digits[:12]
                    break
                elif len(ref_val) >= 9:
                    t.reference_number = ref_digits.upper()
                    break

        # 4. Extract Date and Time
        # Check patterns in entire text or line by line
        if re.search(r'\byesterday\b', self.raw_text, re.IGNORECASE):
            from datetime import timedelta
            from utils.dates import get_current_time_in_tz
            t.transaction_date = get_current_time_in_tz().date() - timedelta(days=1)
        elif re.search(r'\btoday\b', self.raw_text, re.IGNORECASE):
            from utils.dates import get_current_time_in_tz
            t.transaction_date = get_current_time_in_tz().date()
        else:
            # Date e.g. 04Sep2026, 31 Aug 2026, 04/09/2026
            date_match = re.search(
                r'(\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(?:\d{2,4})?)',
                self.raw_text, re.IGNORECASE
            )
            if date_match:
                t.transaction_date = parse_date(date_match.group(1))
            else:
                date_match2 = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', self.raw_text)
                if date_match2:
                    t.transaction_date = parse_date(date_match2.group(1))
                
        # Time: Prefer times with AM/PM (e.g. 10:02 AM) over status bar clock time (e.g. 10:38)
        time_match = re.search(r'(\d{1,2}[:.]\d{2}(?::\d{2})?\s*[aApP][mM])', self.raw_text)
        if not time_match:
            time_match = re.search(r'(\d{1,2}[:.]\d{2}(?::\d{2})?)', self.raw_text)
        if time_match:
            t.transaction_time = parse_time(time_match.group(1))

        # 5. Extract Person Names (From / To)
        # Scan lines to find 'From' and 'To' sections
        for i, line in enumerate(self.lines):
            line_clean = line.strip()
            
            # Check "Paid to <Name>" / "Sent to <Name>" / "Transfer to <Name>"
            sent_to_match = re.search(r'(?:paid|sent|transfer(?:red)?|payment)\s+to\s+([a-zA-Z\s]+?)(?=\s+paid|\s+sent|\s+received|\s+via|\s+for|\s+on|\s+using|\s+at|\s+\d|$|\n)', line_clean, re.IGNORECASE)
            if not sent_to_match:
                sent_to_match = re.search(r'(?:paid|sent|transfer(?:red)?|payment)\s+to\s+([a-zA-Z\s]+)', line_clean, re.IGNORECASE)
            if sent_to_match:
                val = clean_person_name(sent_to_match.group(1))
                if val:
                    t.recipient_name = val
                    
            # Check "Received from <Name>" / "Received <amt> from <Name>"
            recv_from_match = re.search(r'(?:received|money\s+received).*?\bfrom\s+([a-zA-Z\s]+?)(?=\s+via|\s+for|\s+on|\s+using|\s+at|$|\n)', line_clean, re.IGNORECASE)
            if recv_from_match:
                val = clean_person_name(recv_from_match.group(1))
                if val:
                    t.sender_name = val

            # Check general "from <Name> via/for/on/using" in conversational text
            if not t.sender_name:
                conv_from = re.search(r'\bfrom\s+([a-zA-Z\s]+?)(?=\s+via|\s+for|\s+on|\s+using|\s+at|$|\n)', line_clean, re.IGNORECASE)
                if conv_from:
                    val = clean_person_name(conv_from.group(1))
                    if val and "bank" not in val.lower() and "phonepe" not in val.lower() and "paytm" not in val.lower():
                        t.sender_name = val

            # Check general "to <Name> via/for/on/using" in conversational text
            if not t.recipient_name:
                conv_to = re.search(r'\bto\s+([a-zA-Z\s]+?)(?=\s+via|\s+for|\s+on|\s+using|\s+at|$|\n)', line_clean, re.IGNORECASE)
                if conv_to:
                    val = clean_person_name(conv_to.group(1))
                    if val and "bank" not in val.lower() and "phonepe" not in val.lower() and "paytm" not in val.lower():
                        t.recipient_name = val

            # Check standalone "From"
            if re.match(r'^from\b', line_clean, re.IGNORECASE):
                val = re.sub(r'^from\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                if not val and i + 1 < len(self.lines):
                    val = self.lines[i + 1]
                val = clean_person_name(val)
                if val and not t.sender_name:
                    t.sender_name = val

            # Check standalone "To"
            elif re.match(r'^to\b', line_clean, re.IGNORECASE):
                val = re.sub(r'^to\s*[:.-]*\s*', '', line_clean, flags=re.IGNORECASE).strip()
                if not val and i + 1 < len(self.lines):
                    val = self.lines[i + 1]
                val = clean_person_name(val)
                if val and not t.recipient_name:
                    t.recipient_name = val

        # 6. Extract Bank and Account Information
        for i, line in enumerate(self.lines):
            line_clean = line.strip()
            # Account number (e.g. Account Number: XXXXXXXX7751 or Bank-1185 or on next line)
            if not t.bank_account:
                acc_match = re.search(r'(?:account|acct|a/c)\s*(?:number|no)?[\s:.-]*[xX*]*([0-9]{3,6})\b', line_clean, re.IGNORECASE)
                if acc_match:
                    t.bank_account = acc_match.group(1)
                elif re.search(r'[-–\s]+([0-9]{4})[a-zA-Z]*$', line_clean):
                    t.bank_account = re.search(r'[-–\s]+([0-9]{4})[a-zA-Z]*$', line_clean).group(1)
                elif "bank" in line_clean.lower() and i + 1 < len(self.lines):
                    next_acc = re.search(r'^([0-9]{4})\b', self.lines[i + 1].strip())
                    if next_acc:
                        t.bank_account = next_acc.group(1)

            # Bank Name (e.g. SBI, HDFC, ICICI, Axis, PNB, Bank of Baroda, Union Bank)
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

            # 7. Extract from UPI Banking Narration (e.g. UPI/DR/XXXXXXXX5052/Mohamata/SBIN/XXXXXX4778/Pay t)
            narr_person = re.search(r'UPI/(?:DR|CR)/[0-9a-zA-Z*xX]+/([^/]+)/', line_clean, re.IGNORECASE)
            if narr_person and not t.person_name:
                val = clean_person_name(narr_person.group(1))
                if val:
                    t.person_name = val
                    if 'DR' in line_clean.upper():
                        t.recipient_name = val
                        t.transaction_type = 'SENT'
                    elif 'CR' in line_clean.upper():
                        t.sender_name = val
                        t.transaction_type = 'RECEIVED'

        # Set consolidated person_name
        if t.transaction_type == 'SENT':
            t.person_name = t.person_name or t.recipient_name or t.sender_name
        else:
            t.person_name = t.person_name or t.sender_name or t.recipient_name

        # Default date if not parsed
        if not t.transaction_date:
            from utils.dates import get_current_time_in_tz
            t.transaction_date = get_current_time_in_tz().date()

        t.payment_status = "SUCCESS" if t.transaction_type else "UNKNOWN"
        t.payment_app = "Generic"
        
        return t
