import unittest
from decimal import Decimal

from utils.validation import (
    parse_decimal_amount,
    validate_caption,
    validate_name,
    validate_reference,
    validate_string_length,
    validate_transaction_type,
    validate_uid,
)


class TestValidationMatrix(unittest.TestCase):
    """
    Tests the validation matrix for monetary amounts, transaction types,
    UID formats, and string length boundaries as specified in Prompt 2.
    """

    def test_parse_decimal_amount_matrix(self):
        # 1. NaN and Infinities (both float and string representations)
        for invalid_val in [
            float("nan"),
            float("inf"),
            -float("inf"),
            "nan",
            "NaN",
            "sNaN",
            "inf",
            "+inf",
            "-inf",
            "infinity",
            "Infinity",
            "+infinity",
            "-infinity",
        ]:
            with self.subTest(val=invalid_val), self.assertRaises(ValueError):
                parse_decimal_amount(invalid_val)

        # 2. Negative numbers
        for neg_val in [-5, -5.0, "-5", "-5.00", -0.01, "-0.01", Decimal("-5.00")]:
            with self.subTest(val=neg_val), self.assertRaises(ValueError):
                parse_decimal_amount(neg_val)

        # 3. Zero (disallowed by default, allowed when allow_zero=True)
        for zero_val in [0, 0.0, "0", "0.00", Decimal(0)]:
            with self.subTest(val=zero_val, allow_zero=False), self.assertRaises(ValueError):
                parse_decimal_amount(zero_val, allow_zero=False)

            with self.subTest(val=zero_val, allow_zero=True):
                result = parse_decimal_amount(zero_val, allow_zero=True)
                self.assertEqual(result, Decimal("0.00"))

        # 4. Formatted string inputs (commas, rupee symbols, spaces)
        formatted_cases = [
            ("1,234.50", Decimal("1234.50")),
            ("₹1,234.50", Decimal("1234.50")),
            ("₹ 1,234.50", Decimal("1234.50")),
            ("Rs. 1,234.50", Decimal("1234.50")),
            ("Rs.1,234.50", Decimal("1234.50")),
            ("rs 1,234.50", Decimal("1234.50")),
            ("INR 1,234.50", Decimal("1234.50")),
            ("  1,234.50  ", Decimal("1234.50")),
            ("10,00,000.50", Decimal("1000000.50")),
            ("500", Decimal("500.00")),
            (500, Decimal("500.00")),
            (500.25, Decimal("500.25")),
        ]
        for raw_str, expected in formatted_cases:
            with self.subTest(raw=raw_str):
                self.assertEqual(parse_decimal_amount(raw_str), expected)

        # 5. Malformed text, empty values, invalid types
        for malformed in ["abc", "", "   ", None, [], {}, "12.34.56", "₹", "Rs."]:
            with self.subTest(val=malformed), self.assertRaises((ValueError, TypeError)):
                parse_decimal_amount(malformed)

        # 6. Maximum limit boundary (10 crore = 100,000,000.00)
        # Exactly 10 crore should succeed
        self.assertEqual(
            parse_decimal_amount("100000000.00"),
            Decimal("100000000.00")
        )
        self.assertEqual(
            parse_decimal_amount(100000000),
            Decimal("100000000.00")
        )

        # Exceeding 10 crore must be rejected
        for huge in [
            "100000000.01",
            100000001,
            "100000001",
            "500000000",
            Decimal("100000000.01"),
        ]:
            with self.subTest(val=huge), self.assertRaises(ValueError):
                parse_decimal_amount(huge)

    def test_validate_transaction_type(self):
        self.assertEqual(validate_transaction_type("SENT"), "SENT")
        self.assertEqual(validate_transaction_type("RECEIVED"), "RECEIVED")
        self.assertEqual(validate_transaction_type("sent"), "SENT")
        self.assertEqual(validate_transaction_type("received"), "RECEIVED")
        self.assertEqual(validate_transaction_type("  SENT  "), "SENT")

        # Invalid types must be rejected
        for invalid in ["TRANSFER", "SPENT", "PAID", "CREDIT", "DEBIT", "", None, 123]:
            with self.subTest(val=invalid), self.assertRaises(ValueError):
                validate_transaction_type(invalid)

    def test_validate_uid(self):
        valid_uid = "0123456789abcdef0123456789abcdef"
        self.assertEqual(validate_uid(valid_uid), valid_uid)
        # Uppercase hex is normalized to lowercase
        self.assertEqual(validate_uid(valid_uid.upper()), valid_uid)

        # Invalid lengths and characters
        for invalid_uid in [
            "0123456789abcdef0123456789abcde",    # 31 chars
            "0123456789abcdef0123456789abcdef0",   # 33 chars
            "0123456789abcdef0123456789abcdeg",   # 'g' is non-hex
            "",
            None,
            "not-a-valid-uid-at-all",
        ]:
            with self.subTest(val=invalid_uid), self.assertRaises(ValueError):
                validate_uid(invalid_uid)

    def test_validate_string_lengths(self):
        # Generic string length validator
        self.assertEqual(validate_string_length("Hello", max_length=10), "Hello")
        with self.assertRaises(ValueError):
            validate_string_length("Toolongstring", max_length=5)

        # Name: max 120 chars
        self.assertEqual(validate_name("Alice"), "Alice")
        self.assertEqual(validate_name("A" * 120), "A" * 120)
        with self.assertRaises(ValueError):
            validate_name("A" * 121)

        # Reference: max 100 chars
        self.assertEqual(validate_reference("UPI123456789"), "UPI123456789")
        self.assertEqual(validate_reference("R" * 100), "R" * 100)
        with self.assertRaises(ValueError):
            validate_reference("R" * 101)

        # Caption: max 1024 chars
        self.assertEqual(validate_caption("C" * 1024), "C" * 1024)
        with self.assertRaises(ValueError):
            validate_caption("C" * 1025)

        # Required fields
        with self.assertRaises(ValueError):
            validate_name("", required=True)
        with self.assertRaises(ValueError):
            validate_name(None, required=True)


if __name__ == "__main__":
    unittest.main()
