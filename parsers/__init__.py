from parsers.base import BasePaymentParser
from parsers.generic import GenericParser
from parsers.phonepe import PhonePeParser
from parsers.googlepay import GooglePayParser
from parsers.paytm import PaytmParser
from parsers.bhim import BhimParser
from parsers.extended_upi import (
    CredParser, SuperMoneyParser, NaviParser, YonoSbiParser, UnionEaseParser
)

def get_best_parser(raw_text: str) -> BasePaymentParser:
    """Returns the most appropriate parser based on the raw text."""
    # Order matters: Specific parsers first, Generic last
    parsers = [
        PaytmParser(raw_text),
        BhimParser(raw_text),
        PhonePeParser(raw_text),
        GooglePayParser(raw_text),
        CredParser(raw_text),
        SuperMoneyParser(raw_text),
        NaviParser(raw_text),
        YonoSbiParser(raw_text),
        UnionEaseParser(raw_text),
        GenericParser(raw_text)
    ]
    
    for parser in parsers:
        if parser.can_parse():
            return parser
            
    return parsers[-1] # Fallback to generic
