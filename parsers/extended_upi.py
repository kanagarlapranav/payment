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
        return "supermoney" in text_l or "super.money" in text_l or "@supermoney" in text_l or "@super.money" in text_l

    def parse(self) -> Transaction:
        t = super().parse()
        t.payment_app = "Super.money"
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
