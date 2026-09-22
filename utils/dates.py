from datetime import date, datetime, timezone
import re
from typing import Any
try:
    import zoneinfo
except ImportError:
    zoneinfo = None

try:
    import pytz
except ImportError:
    pytz = None

from config import DEFAULT_TIMEZONE, logger

def utc_now_iso() -> str:
    """Returns timezone-aware UTC datetime formatted as ISO 8601 with microseconds."""
    return datetime.now(timezone.utc).isoformat()

def build_occurred_at(tx_date: Any, tx_time: Any) -> str:
    """
    Builds a canonical occurred_at timestamp string in 'YYYY-MM-DD HH:MM:SS' format
    from transaction_date and transaction_time (12-hour or 24-hour).
    If tx_time is blank or unparsable, defaults to '00:00:00' (logging unparsable inputs).
    """
    # 1. Parse and canonicalize date part
    date_part = None
    if isinstance(tx_date, (datetime, date)):
        date_part = tx_date.strftime("%Y-%m-%d")
    elif tx_date:
        s_date = str(tx_date).strip()
        # Direct YYYY-MM-DD match
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", s_date)
        if m:
            date_part = m.group(1)
        else:
            parsed = parse_date(s_date)
            if parsed:
                date_part = parsed.strftime("%Y-%m-%d")

    if not date_part:
        date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 2. Parse and canonicalize time part
    time_part = "00:00:00"
    if tx_time is not None:
        raw_t = str(tx_time).strip()
        if raw_t:
            cleaned_t = re.sub(r"\s+", " ", raw_t).upper().replace(".", ":")
            # Separate digits and AM/PM if stuck together (e.g. 10:02AM -> 10:02 AM)
            cleaned_t = re.sub(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*([AP]M)", r"\1 \2", cleaned_t)

            time_formats = [
                "%I:%M %p",
                "%I:%M:%S %p",
                "%H:%M:%S",
                "%H:%M",
            ]
            parsed_time = None
            for fmt in time_formats:
                try:
                    parsed_time = datetime.strptime(cleaned_t, fmt).time()
                    time_part = parsed_time.strftime("%H:%M:%S")
                    break
                except ValueError:
                    continue

            if not parsed_time:
                logger.warning(f"Unparsable transaction_time: {tx_time!r}, defaulting to 00:00:00")
                time_part = "00:00:00"

    return f"{date_part} {time_part}"

def get_current_time_in_tz():
    """Returns current datetime in default timezone."""
    if zoneinfo:
        try:
            tz = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
            return datetime.now(tz)
        except Exception:
            pass
    if pytz:
        try:
            tz = pytz.timezone(DEFAULT_TIMEZONE)
            return datetime.now(tz)
        except Exception:
            pass
    from datetime import timedelta
    if DEFAULT_TIMEZONE == 'Asia/Kolkata':
        return datetime.now(timezone(timedelta(hours=5, minutes=30)))
    return datetime.now(timezone.utc)

def parse_date(date_str: str, fallback_year: int = None) -> date:
    """Attempts to parse a date string like '31 Aug', '15 Sept 2026', '06 Sep 2026', or '04Sep2026'."""
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

    # Remove noise prefixes like 'date and time', 'date:', 'on', 'at'
    date_str = re.sub(r'^(?:date\s+and\s+time|date|time|on|at)\s*[:.-]*\s*', '', date_str)

    # Remove ordinals (st, nd, rd, th) and commas
    date_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
    date_str = date_str.replace(',', ' ').replace('.', ' ')

    # Normalize month abbreviations: 'sept' -> 'sep', 'september' -> 'sep'
    date_str = re.sub(r'\bsept\b', 'sep', date_str)

    # Separate stuck digits and letters (e.g. '04sep2026' -> '04 sep 2026')
    date_str = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', date_str)
    date_str = re.sub(r'([a-zA-Z]+)(\d{2,4})', r'\1 \2', date_str)
    date_str = re.sub(r'\s+', ' ', date_str).strip()

    formats = [
        ('%d %b %Y', False), # 15 Sep 2026 / 31 Aug 2026 / 04 Sep 2026
        ('%d %B %Y', False), # 15 September 2026
        ('%d %b %y', False), # 15 Sep 26
        ('%b %d %Y', False), # Sep 15 2026
        ('%B %d %Y', False), # September 15 2026
        ('%d %b', True),     # 15 Sep
        ('%d %B', True),     # 15 September
        ('%Y-%m-%d', False), # 2026-09-15
        ('%d/%m/%Y', False), # 15/09/2026
        ('%d-%m-%Y', False), # 15-09-2026
        ('%d/%m/%y', False), # 15/09/26
        ('%d-%m-%y', False), # 15-09-26
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
    """Cleans up time string, returning standard format like '07:54 PM' or '10:02 AM'."""
    if not time_str:
        return ""
    
    time_str = re.sub(r'\s+', ' ', time_str).strip()
    time_str = re.sub(r'(\d{1,2}:\d{2})\s*([aApP][mM])', r'\1 \2', time_str)
    
    match = re.search(r'(\b(?:0?[1-9]|1[0-2]|2[0-3])[:.]\d{2}(?::\d{2})?\s*(?:[aApP][mM])?)', time_str)
    if match:
        raw_t = match.group(1).upper().replace('.', ':').strip()
        raw_t = re.sub(r'(\d{2})\s*([AP]M)', r'\1 \2', raw_t)
        return raw_t
    return time_str.strip()

def format_display_date(d) -> str:
    """Formats date cleanly as '15 Sep 2026'."""
    if not d:
        return "Unknown Date"
    if isinstance(d, (datetime, date)):
        return d.strftime("%d %b %Y")
    parsed = parse_date(str(d))
    if parsed:
        return parsed.strftime("%d %b %Y")
    return str(d)

def parse_utc_iso(ts_str: Any) -> datetime | None:
    """
    Parses an ISO 8601 timestamp string into a timezone-aware UTC datetime.
    Handles 'Z', timezone offsets, naive timestamps (assumed UTC), and datetime objects.
    Returns None if parsing fails or input is empty.
    """
    if not ts_str:
        return None
    if isinstance(ts_str, datetime):
        if ts_str.tzinfo is None:
            return ts_str.replace(tzinfo=timezone.utc)
        return ts_str.astimezone(timezone.utc)
    s = str(ts_str).strip()
    if not s:
        return None
    try:
        if s.endswith('Z') or s.endswith('z'):
            s = s[:-1] + '+00:00'
        # Handle space separator instead of 'T'
        if ' ' in s and 'T' not in s:
            s = s.replace(' ', 'T')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception as e:
        logger.debug(f"Failed to parse timestamp {ts_str!r}: {e}")
        return None

