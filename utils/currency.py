import re

NUMBER_WORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    'hundred': 100, 'thousand': 1000, 'lakh': 100000, 'lakhs': 100000, 'crore': 10000000, 'crores': 10000000, 'million': 1000000
}

def words_to_number(text: str) -> float:
    """Converts words like 'Rupees Six Thousand Two Hundred Only' to 6200.0."""
    if not text:
        return 0.0
    tokens = re.findall(r'[a-zA-Z]+', text.lower())
    total = 0.0
    current = 0.0
    found_any = False
    
    for word in tokens:
        if word in NUMBER_WORDS:
            found_any = True
            val = NUMBER_WORDS[word]
            if val == 100:
                if current == 0: current = 1
                current *= val
            elif val in (1000, 100000, 1000000, 10000000):
                if current == 0: current = 1
                total += current * val
                current = 0
            else:
                current += val
    total += current
    return float(total) if found_any and total > 0 else 0.0

def parse_amount(amount_str: str) -> float:
    """
    Normalizes amount strings to numeric float.
    Examples: '₹5,000' -> 5000.0, 'Rs 5,000' -> 5000.0, 'R30,700' -> 30700.0,
    'Rupees Six Thousand Two Hundred Only' -> 6200.0
    """
    if not amount_str:
        return 0.0

    if isinstance(amount_str, (int, float)):
        return float(amount_str)

    # Clean string
    raw_str = str(amount_str).strip()
    
    # 1. Try words to number first if text contains "Rupees" or word numbers
    if any(w in raw_str.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'only')):
        w_amt = words_to_number(raw_str)
        if w_amt > 0:
            return w_amt

    # 2. Try numeric parsing
    cleaned = re.sub(r'^[^\d₹$€£?RsINRinr]*[₹$€£?]\s*', '', raw_str, flags=re.IGNORECASE)
    cleaned = re.sub(r'^(?:Rs\.?|INR|rupees?)\s*', '', cleaned, flags=re.IGNORECASE)
    # Handle OCR artifact where ₹ is read as 'R', 'r', 'F', 'f' right before digits e.g. R30,700, F4000
    cleaned = re.sub(r'^[RrFf](?=\d)', '', cleaned)
    
    # Remove commas
    cleaned = cleaned.replace(',', '').replace(' ', '')
    
    # Try parsing
    try:
        match = re.search(r'(\d+(?:\.\d{1,2})?)', cleaned)
        if match:
            return float(match.group(1))
    except ValueError:
        pass
        
    return 0.0

def format_currency(amount: float) -> str:
    """Formats float to currency string e.g. 5000.0 -> ₹5,000"""
    # Indian formatting style (e.g. 1,00,000) could be complex, simple comma grouping for now
    return f"₹{amount:,.0f}" if amount.is_integer() else f"₹{amount:,.2f}"
