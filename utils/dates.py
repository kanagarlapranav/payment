import re
from datetime import datetime, date
import pytz
from config import DEFAULT_TIMEZONE

def get_current_time_in_tz():
    """Returns current datetime in default timezone."""
    tz = pytz.timezone(DEFAULT_TIMEZONE)
    return datetime.now(tz)

def parse_date(date_str: str, fallback_year: int = None) -> date:
    """Attempts to parse a date string like '31 Aug', '06 Sep 2026', or '04Sep2026'."""
    if not date_str:
        return None
        
    date_str = date_str.strip().lower()

    if date_str in ('yesterday', 'yesterdays'):
        from datetime import timedelta
        return get_current_time_in_tz().date() - timedelta(days=1)
    if date_str in ('today', 'todays'):
        return get_current_time_in_tz().date()
    
    if not fallback_year:
        fallback_year = get_current_time_in_tz().year

    # Remove ordinals (st, nd, rd, th) and commas
    date_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
    date_str = date_str.replace(',', ' ').replace('.', ' ')

    # Separate stuck digits and letters (e.g. '04sep2026' -> '04 sep 2026')
    date_str = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', date_str)
    date_str = re.sub(r'([a-zA-Z]+)(\d{2,4})', r'\1 \2', date_str)
    date_str = re.sub(r'\s+', ' ', date_str).strip()

    formats = [
        ('%d %b %Y', False), # 31 Aug 2026 / 04 Sep 2026
        ('%d %B %Y', False), # 31 August 2026
        ('%d %b %y', False), # 31 Aug 26
        ('%d %b', True),     # 31 Aug
        ('%d %B', True),     # 31 August
        ('%Y-%m-%d', False), # 2026-08-31
        ('%d/%m/%Y', False), # 31/08/2026
        ('%d-%m-%Y', False), # 31-08-2026
        ('%d/%m/%y', False), # 31/08/26
        ('%d-%m-%y', False), # 31-08-26
    ]

    for fmt, needs_year in formats:
        try:
            if needs_year:
                parsed_date = datetime.strptime(f"{date_str} {fallback_year}", f"{fmt} %Y").date()
            else:
                parsed_date = datetime.strptime(date_str, fmt).date()
            return parsed_date
        except ValueError:
            continue
            
    return None

def parse_time(time_str: str) -> str:
    """Cleans up time string, returning standard format like '08:25 PM' or '10:02 AM'."""
    if not time_str:
        return ""
    
    # Collapse newlines and multiple spaces to a single space
    time_str = re.sub(r'\s+', ' ', time_str).strip()
    
    # Regex to find time-like patterns: valid hours (1-12 or 00-23)
    match = re.search(r'(\b(?:0?[1-9]|1[0-2]|2[0-3])[:.]\d{2}(?::\d{2})?\s*(?:[aApP][mM])?)', time_str)
    if match:
        raw_t = match.group(1).upper().replace('.', ':').strip()
        # Ensure single space before AM/PM (e.g. 10:02AM -> 10:02 AM)
        raw_t = re.sub(r'(\d{2})\s*([AP]M)', r'\1 \2', raw_t)
        return raw_t
    return time_str.strip()

def format_display_date(d) -> str:
    """Formats date cleanly as '06 Sep 2026'."""
    if not d:
        return "Unknown Date"
    if isinstance(d, (datetime, date)):
        return d.strftime("%d %b %Y")
    parsed = parse_date(str(d))
    if parsed:
        return parsed.strftime("%d %b %Y")
    return str(d)

