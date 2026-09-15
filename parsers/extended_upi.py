import re
from parsers.generic import GenericParser
from parsers.base import clean_person_name, split_camel_case
from database.models import Transaction
from utils.dates import parse_date, parse_time
from utils.currency import parse_amount


class CredParser(GenericParser):
    """Parser specifically tuned for CRED / CRED UPI payment receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "cred" in text_l or "@cred" in text_l or "cred upi" in text_l or "cred protected" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "CRED"
        return t


class SuperMoneyParser(GenericParser):
    """Parser specifically tuned for Flipkart Super.money UPI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return (
            "supermoney" in text_l or "super.money" in text_l or 
            "@supermoney" in text_l or "@super.money" in text_l or 
            "@superyes" in text_l or "@superaxis" in text_l or
            ("super" in text_l and "money" in text_l)
        )

    def parse(self) -> Transaction:
        t = Transaction()
        t.ocr_text = self.raw_text
        t.payment_app = "Super.money"
        t.transaction_type = "SENT"
        
        text_l = self.raw_text.lower()
        lines = [l.strip() for l in self.raw_text.split('\n') if l.strip()]
        
        # 1. Transaction Type
        if "received" in text_l or "credited" in text_l:
            t.transaction_type = "RECEIVED"
            
        # 2. Extract Amount
        # In Super.money, amount is right after "Payment Successful" / "Paid Successfully"
        from utils.currency import extract_amounts_from_line
        for i, line in enumerate(lines):
            ll = line.lower()
            if "payment successful" in ll or "paid successfully" in ll or "transferred successfully" in ll:
                for offset in (1, 2, 3):
                    if i + offset < len(lines):
                        cand_line = lines[i + offset]
                        cands = extract_amounts_from_line(cand_line)
                        valid_cands = [c for c in cands if not self.is_invalid_amount(c, cand_line)]
                        if valid_cands:
                            t.amount = valid_cands[0]
                            break
                if t.amount > 0:
                    break
                    
        # Fallback to general amount extraction if not found
        if not t.amount or t.amount <= 0:
            gen_t = super().parse()
            t.amount = gen_t.amount
            if not t.person_name:
                t.person_name = gen_t.person_name
            if not t.reference_number:
                t.reference_number = gen_t.reference_number
                
        # 3. Extract Recipient / Sender Name & UPI ID
        for line in lines:
            line_clean = line.strip()
            # To: VIKRAMAN NAIR K or TO:VIKRAMANNAIRK
            m_to = re.search(r'\bto\s*[:\-]?\s*([A-Za-z\s]+)', line_clean, re.IGNORECASE)
            if m_to and not t.recipient_name:
                name_cand = m_to.group(1).strip()
                if len(name_cand) >= 2 and not any(kw in name_cand.lower() for kw in ('super', 'money', 'upi', 'bank', 'successful', 'amount')):
                    t.recipient_name = split_camel_case(clean_person_name(name_cand))
                    
            # From: KANAGARLA PRANAV
            m_from = re.search(r'\bfrom\s*[:\-]?\s*([A-Za-z\s]+)', line_clean, re.IGNORECASE)
            if m_from and not t.sender_name:
                name_cand = m_from.group(1).strip()
                if len(name_cand) >= 2 and not any(kw in name_cand.lower() for kw in ('super', 'money', 'upi', 'bank')):
                    t.sender_name = split_camel_case(clean_person_name(name_cand))

            # UPI ID
            m_upi = re.search(r'([a-zA-Z0-9.\-_]+@[a-zA-Z0-9]+)', line_clean)
            if m_upi and not t.upi_id:
                t.upi_id = m_upi.group(1)

            # Reference Number / UTR
            m_ref = re.search(r'(?:UPI\s*reference\s*ID|ref\s*(?:no|id)?|UTR)\s*[:\-]?\s*(\d{10,16})', line_clean, re.IGNORECASE)
            if m_ref and not t.reference_number:
                t.reference_number = m_ref.group(1)
                
            # Date & Time e.g. "September 15 at 1:41PM"
            if not t.transaction_date or t.transaction_date.strftime("%Y-%m-%d") == parse_date("Today").strftime("%Y-%m-%d"):
                m_dt = re.search(r'([A-Za-z]+\s+\d{1,2}(?:\s*,\s*\d{4})?)\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM)?)', line_clean, re.IGNORECASE)
                if m_dt:
                    parsed_d = parse_date(m_dt.group(1))
                    if parsed_d:
                        t.transaction_date = parsed_d
                    t.transaction_time = m_dt.group(2).strip()

        if t.transaction_type == "SENT":
            t.person_name = t.recipient_name or t.person_name or "Unknown"
        else:
            t.person_name = t.sender_name or t.person_name or "Unknown"

        # Bank Name
        for line in lines:
            ll = line.lower()
            if any(b in ll for b in ('federal bank', 'hdfc', 'sbi', 'icici', 'axis', 'yes bank', 'kotak')):
                t.bank_name = line.strip()
                break

        return t


class NaviParser(GenericParser):
    """Parser specifically tuned for Navi / NaviPay UPI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "navi" in text_l or "navipay" in text_l or "@navi" in text_l or "navi technologies" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "Navi"
        return t


class YonoSbiParser(GenericParser):
    """Parser specifically tuned for YONO SBI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "yono" in text_l or "yono sbi" in text_l or ("sbi upi" in text_l)

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "YONO SBI"
        if not t.bank_name:
            t.bank_name = "State Bank of India"
        return t


class UnionEaseParser(GenericParser):
    """Parser specifically tuned for Union EASE / Vyom Union Bank receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "union ease" in text_l or "unionease" in text_l or "vyom" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "Union EASE"
        if not t.bank_name:
            t.bank_name = "Union Bank of India"
        return t


class WhatsAppPayParser(GenericParser):
    """Parser specifically tuned for WhatsApp Pay UPI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "whatsapp" in text_l or "@waaxis" in text_l or "@wahdfcbank" in text_l or "@waicici" in text_l or "@wasbi" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "WhatsApp Pay"
        return t


class MobikwikParser(GenericParser):
    """Parser specifically tuned for Mobikwik UPI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "mobikwik" in text_l or "@ikwik" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "MobiKwik"
        return t


class SliceParser(GenericParser):
    """Parser specifically tuned for Slice UPI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "slice" in text_l or "@slice" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "Slice"
        return t


class JupiterParser(GenericParser):
    """Parser specifically tuned for Jupiter / Federal Bank UPI receipts and screenshots."""

    def can_parse(self) -> bool:
        text_l = self.raw_text.lower()
        return "jupiter" in text_l or "@jupiteraxis" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "Jupiter"
        return t
