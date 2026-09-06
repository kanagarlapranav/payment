from parsers.base import BasePaymentParser
from parsers.generic import GenericParser
from parsers.phonepe import PhonePeParser
from parsers.googlepay import GooglePayParser
from parsers.paytm import PaytmParser

def get_best_parser(raw_text: str) -> BasePaymentParser:
    """Returns the most appropriate parser based on the raw text."""
    # Order matters: Specific parsers first, Generic last
    parsers = [
        PaytmParser(raw_text),
        PhonePeParser(raw_text),
        GooglePayParser(raw_text),
        GenericParser(raw_text)
    ]
    
    for parser in parsers:
        if parser.can_parse():
            return parser
            
    return parsers[-1] # Fallback to generic
