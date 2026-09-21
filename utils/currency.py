import re

NUMBER_WORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    'hundred': 100, 'thousand': 1000, 'lakh': 100000, 'lakhs': 100000, 'lac': 100000, 'lacs': 100000,
    'crore': 10000000, 'crores': 10000000, 'million': 1000000
}

def words_to_number(text: str) -> float:
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
                if current == 0:
                    current = 1
                current *= val
            elif val in (1000, 100000, 1000000, 10000000):
                if current == 0:
                    current = 1
                total += current * val
                current = 0
            else:
                current += val
    total += current
    return float(total) if found_any and total > 0 else 0.0


def normalize_amount_string(raw: str) -> float:
    if not raw:
        return 0.0

    s = str(raw).strip()
    
    # 1. Clean prefixes: currency symbols (₹, $, €, £, ¥, Rs, INR, ?, *, etc.) and OCR letter artifacts (R, r, F, f)
    s = re.sub(r'^[^\d₹$€£¥?*RsINRinr]*[₹$€£¥?*]\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^(?:Rs\.?|INR|rupees?)\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^[RrFftz](?=\d)', '', s)
    s = s.strip()

    if not s:
        return 0.0

    # 2. Clean spaces around commas or dots e.g. "3, 500" -> "3,500"
    s = re.sub(r'\s*([,\.])\s*', r'\1', s)

    # 3. Clean spaces between digit blocks e.g. "3 500" -> "3500"
    if re.search(r'^\d+(?:\s+\d+)+(?:\.\d{1,2})?$', s):
        s = re.sub(r'\s+', '', s)

    s = s.replace(' ', '')

    # 4. Check for European format e.g. "3.500,00" -> "3500.00"
    if re.search(r'^\d{1,3}(?:\.\d{3})+,\d{2}$', s):
        s = s.replace('.', '').replace(',', '.')

    # 5. Check for standard Indian / US format with decimals e.g. "3,500.00"
    if re.search(r'^\d{1,3}(?:,\d{2,3})+\.\d{1,2}$', s):
        s = s.replace(',', '')
        try:
            return float(s)
        except ValueError:
            pass

    # 6. Check for dot as thousands/lakhs separator e.g. "3.500" or "35.000"
    if '.' in s and ',' not in s:
        parts = s.split('.')
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit() and parts[1].isdigit()):
            s = s.replace('.', '')
            try:
                return float(s)
            except ValueError:
                pass

    # 7. Standard comma removal e.g. "3,500", "30,700"
    s = s.replace(',', '')

    try:
        return float(s)
    except ValueError:
        m = re.search(r'(\d+(?:\.\d{1,2})?)', s)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass

    return 0.0


def extract_amounts_from_line(line: str) -> list[float]:
    if not line:
        return []

    if any(w in line.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'only')) and any(w in line.lower() for w in ('rupees', 'rs', 'inr', 'only', 'amount')):
        w_val = words_to_number(line)
        if w_val > 0:
            return [w_val]

    # Pre-clean masked account numbers like ****7751, ***1185, xx7751
    cleaned_line = re.sub(r'[*xX]{2,}\s*\d{3,6}\b', ' ', line)
    cleaned_line = re.sub(r'(?:a/c|acct|account|ending in)\s*[:.-]*\s*[xX*]*\d{3,6}\b', ' ', cleaned_line, flags=re.IGNORECASE)
    
    # Pre-clean date and time strings from the line
    cleaned_line = re.sub(r'\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[aApP][mM])?\b', ' ', cleaned_line)
    cleaned_line = re.sub(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|October|Nov|Dec)[a-z]*\s*(?:\d{2,4})?\b', ' ', cleaned_line, flags=re.IGNORECASE)
    cleaned_line = re.sub(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', ' ', cleaned_line)
    cleaned_line = re.sub(r'[a-zA-Z0-9*.\-_]+@[a-zA-Z0-9.\-_]+', ' ', cleaned_line)

    results = []
    # 1. Prefixed amounts (₹, Rs, INR, ?, *, R, F)
    candidate_regex = re.finditer(
        r'(?:[₹$€£¥?*]|Rs\.?|INR|[RrFftz](?=\d))\s*(\d+(?:[,\.\s]\s*\d{2,3})*(?:\.\d{1,2})?|\d+)',
        cleaned_line,
        re.IGNORECASE
    )

    for m in candidate_regex:
        full_match = m.group(0).strip()
        if not full_match:
            continue
        val = normalize_amount_string(full_match)
        if val > 0:
            results.append(val)

    # 2. Standalone formatted amounts (e.g. 3,500 or 3.500 or 35,000.00)
    if not results:
        plain_regex = re.finditer(
            r'\b(\d{1,3}(?:[,\.]\d{2,3})+(?:\.\d{1,2})?|\d+\.\d{2})\b',
            cleaned_line
        )
        for m in plain_regex:
            val = normalize_amount_string(m.group(0))
            if val > 0:
                results.append(val)

    # 3. Plain integer numbers (only if reasonably short, 1 to 6 digits)
    if not results:
        plain_int = re.finditer(r'\b(\d{1,6})\b', cleaned_line)
        for m in plain_int:
            val = normalize_amount_string(m.group(0))
            if val > 0 and val not in (2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030):
                results.append(val)

    return results


def parse_amount(amount_str: str) -> float:
    if not amount_str:
        return 0.0

    if isinstance(amount_str, (int, float)):
        return float(amount_str)

    raw_str = str(amount_str).strip()
    
    if any(w in raw_str.lower() for w in ('thousand', 'hundred', 'lakh', 'crore', 'only')):
        w_amt = words_to_number(raw_str)
        if w_amt > 0:
            return w_amt

    candidates = extract_amounts_from_line(raw_str)
    if candidates:
        return candidates[0]

    return normalize_amount_string(raw_str)


def format_indian_number(num: int) -> str:
    s = str(abs(num))
    if len(s) <= 3:
        res = s
    else:
        last_three = s[-3:]
        remaining = s[:-3]
        chunks = []
        while len(remaining) > 2:
            chunks.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            chunks.insert(0, remaining)
        res = ",".join(chunks) + "," + last_three
    return ("-" if num < 0 else "") + res


def format_currency(amount: float) -> str:
    if amount is None:
        return '₹0'
    try:
        amt = float(amount)
    except (ValueError, TypeError):
        return '₹0'
        
    is_neg = amt < 0
    abs_amt = abs(amt)
    
    if abs_amt.is_integer():
        formatted = format_indian_number(int(abs_amt))
    else:
        int_part = int(abs_amt)
        dec_part = f"{abs_amt:.2f}".split('.')[1]
        formatted = f"{format_indian_number(int_part)}.{dec_part}"
        
    return f"-₹{formatted}" if is_neg else f"₹{formatted}"
