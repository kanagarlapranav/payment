from parsers.base import BasePaymentParser
from parsers.generic import GenericParser
from parsers.phonepe import PhonePeParser
from parsers.googlepay import GooglePayParser
from parsers.paytm import PaytmParser
from parsers.bhim import BhimParser
from parsers.amazonpay import AmazonPayParser
from parsers.extended_upi import (
    CredParser, SuperMoneyParser, NaviParser, YonoSbiParser, UnionEaseParser,
    WhatsAppPayParser, MobikwikParser, SliceParser, JupiterParser
)

def get_best_parser(raw_text: str) -> BasePaymentParser:
    """Returns the most appropriate parser based on the raw text."""
    lines = [l.strip().lower() for l in raw_text.split('\n') if l.strip()]
    top_few = ' '.join(lines[:4])
    text_lower = raw_text.lower()
    
    # 1. Check primary app headers and specific signature keywords
    if any(k in top_few for k in ('amazon pay', 'amazonpay')) or 'amazon reference id' in text_lower or '@apl' in text_lower or '@rapl' in text_lower:
        return AmazonPayParser(raw_text)
    elif any(k in top_few for k in ('paytm', 'paytm payments bank')) or 'upi ref no:' in text_lower or 'p.paytm.me' in text_lower:
        return PaytmParser(raw_text)
    elif 'phonepe transaction id' in text_lower or any(k in top_few for k in ('phonepe', 'phone pe')):
        return PhonePeParser(raw_text)
    elif 'bhim' in top_few or 'banking name' in text_lower or 'payment initiated by' in text_lower:
        return BhimParser(raw_text)
    elif any(k in top_few for k in ('google pay', 'gpay')) or 'google pay' in text_lower or 'gpay' in text_lower:
        return GooglePayParser(raw_text)
    elif 'cred' in top_few or 'cred' in text_lower:
        return CredParser(raw_text)
    elif 'supermoney' in text_lower or 'super.money' in text_lower:
        return SuperMoneyParser(raw_text)
    elif 'navi' in text_lower or 'navipay' in text_lower:
        return NaviParser(raw_text)
    elif 'yono' in text_lower or 'yono sbi' in text_lower:
        return YonoSbiParser(raw_text)
    elif 'union ease' in text_lower or 'unionease' in text_lower or 'vyom' in text_lower:
        return UnionEaseParser(raw_text)
    elif 'whatsapp' in text_lower or '@waaxis' in text_lower:
        return WhatsAppPayParser(raw_text)
    elif 'mobikwik' in text_lower or '@ikwik' in text_lower:
        return MobikwikParser(raw_text)
    elif 'slice' in text_lower or '@slice' in text_lower:
        return SliceParser(raw_text)
    elif 'jupiter' in text_lower or '@jupiteraxis' in text_lower:
        return JupiterParser(raw_text)
    elif 'phonepe' in text_lower:
        return PhonePeParser(raw_text)
    elif 'paytm' in text_lower or '@ptyes' in text_lower or '@paytm' in text_lower:
        return PaytmParser(raw_text)

    # General fallback list with can_parse() check
    parsers = [
        AmazonPayParser(raw_text),
        PhonePeParser(raw_text),
        PaytmParser(raw_text),
        BhimParser(raw_text),
        GooglePayParser(raw_text),
        CredParser(raw_text),
        SuperMoneyParser(raw_text),
        NaviParser(raw_text),
        YonoSbiParser(raw_text),
        UnionEaseParser(raw_text),
        WhatsAppPayParser(raw_text),
        MobikwikParser(raw_text),
        SliceParser(raw_text),
        JupiterParser(raw_text),
        GenericParser(raw_text)
    ]
    
    for parser in parsers:
        if parser.can_parse():
            return parser
            
    return parsers[-1]  # Fallback to generic
