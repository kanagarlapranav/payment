"""
Central validation and precision module for financial amounts and transaction entities.
Ensures mathematical correctness, prevents floating-point inaccuracies,
and blocks corrupted, malicious, or malformed values across all entry points.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

UID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_AMOUNT = Decimal("100000000.00")  # 10 Crore (₹100,000,000.00)
CENT = Decimal("0.01")


def parse_decimal_amount(value: Any, *, allow_zero: bool = False) -> Decimal:
    """
    Parses and validates an amount as a Decimal quantized to 2 decimal places (0.01).
    
    Rejects:
    - None or empty values
    - NaN, sNaN, +Infinity, -Infinity
    - Malformed decimal strings or unparseable objects
    - Negative values (or zero if allow_zero=False)
    - Values exceeding 10 crore (₹100,000,000.00)
    """
    if value is None:
        raise ValueError("Amount cannot be None")

    if isinstance(value, float):
        import math
        if math.isnan(value) or math.isinf(value):
            raise ValueError("Amount must be a finite number (not NaN or Infinity)")
        clean_str = str(value).strip()
    elif isinstance(value, (int, Decimal)):
        clean_str = str(value).strip()
    elif isinstance(value, str):
        clean_str = value.strip()
        # Strip common currency signs, symbols, commas, and whitespace
        for symbol in ["₹", "rs.", "rs", "inr", ","]:
            clean_str = re.sub(re.escape(symbol), "", clean_str, flags=re.IGNORECASE)
        clean_str = clean_str.strip()
    else:
        raise TypeError(f"Invalid amount type: {type(value).__name__}")

    if not clean_str:
        raise ValueError("Amount cannot be empty")

    # Reject text values that represent non-finite floating point numbers
    lower_str = clean_str.lower()
    if lower_str in {"nan", "snan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
        raise ValueError("Amount must be a finite number (not NaN or Infinity)")

    try:
        amount = Decimal(clean_str)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise ValueError(f"Malformed decimal amount: {value!r}") from err

    if not amount.is_finite():
        raise ValueError("Amount must be finite (not NaN or Infinity)")

    if allow_zero:
        if amount < Decimal("0.00"):
            raise ValueError("Amount cannot be negative")
    else:
        if amount <= Decimal("0.00"):
            raise ValueError("Amount must be greater than zero")

    if amount > MAX_AMOUNT:
        raise ValueError(f"Amount {amount} exceeds the maximum limit of 10 crore ({MAX_AMOUNT})")

    return amount.quantize(CENT)


def validate_transaction_type(value: Any) -> str:
    """
    Validates that transaction type is strictly 'SENT' or 'RECEIVED'.
    Never silently falls back to 'SENT'.
    """
    if not value or not isinstance(value, str):
        raise ValueError("Transaction type must be a non-empty string")
    
    norm = value.strip().upper()
    if norm not in {"SENT", "RECEIVED"}:
        raise ValueError(f"Invalid transaction type: {value!r}. Must be 'SENT' or 'RECEIVED'")
    
    return norm


def validate_uid(value: Any) -> str:
    """
    Validates that a UID is an exact 32-character lowercase hexadecimal string.
    """
    if not value or not isinstance(value, str):
        raise ValueError("UID must be a non-empty string")
    
    norm = value.strip().lower()
    if not UID_RE.fullmatch(norm):
        raise ValueError(f"Invalid UID format: {value!r}. Expected 32 lowercase hex characters.")
    
    return norm


def validate_string_length(
    value: Any,
    max_length: int = 255,
    field_name: str = "Field",
    required: bool = False
) -> str:
    """
    Validates string length limits for names, categories, references, and captions.
    """
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return ""
    
    val_str = str(value).strip()
    if required and not val_str:
        raise ValueError(f"{field_name} cannot be empty")
    
    if len(val_str) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length of {max_length} characters (length={len(val_str)})")
    
    return val_str


def validate_name(value: Any, max_length: int = 120, field_name: str = "Name", required: bool = False) -> str:
    """Validates person/merchant/item name string length and format."""
    return validate_string_length(value, max_length=max_length, field_name=field_name, required=required)


def validate_reference(value: Any, max_length: int = 100, field_name: str = "Reference") -> str:
    """Validates transaction reference / UTR string length."""
    return validate_string_length(value, max_length=max_length, field_name=field_name, required=False)


def validate_caption(value: Any, max_length: int = 1024, field_name: str = "Caption") -> str:
    """Validates receipt image caption length."""
    return validate_string_length(value, max_length=max_length, field_name=field_name, required=False)

