import unittest
import os
import re
import subprocess
import logging
from config import SensitiveDataFilter
from ocr.gemini_vision import get_effective_gemini_api_key

class TestNoSecrets(unittest.TestCase):
    """
    Scans tracked source files and verifies credential masking and secret protection.
    """

    TELEGRAM_TOKEN_REGEX = re.compile(r'\b\d{8,11}:[A-Za-z0-9_-]{35}\b')
    AIZA_KEY_REGEX = re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')
    PRIVATE_KEY_REGEX = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----', re.IGNORECASE)

    # Allowed dummy/test placeholders that should not fail scans
    ALLOWED_TEST_TOKENS = {
        "123456789:AAFakePlaceholderBotTokenForTesting35",
        "AIzaSyFakePlaceholderGeminiApiKeyForTesting35",
    }

    def test_no_secrets_in_tracked_files(self):
        """Scans all tracked git files to ensure no live credentials remain in source code."""
        try:
            tracked_files = subprocess.check_output(['git', 'ls-files'], text=True).splitlines()
        except Exception:
            # Fallback if git is unavailable
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tracked_files = []
            for root, dirs, files in os.walk(repo_root):
                if any(ignored in root for ignored in ['venv', '.git', '__pycache__', '.pytest_cache']):
                    continue
                for f in files:
                    tracked_files.append(os.path.relpath(os.path.join(root, f), repo_root))

        violations = []
        for rel_path in tracked_files:
            # Skip test files and fixtures from scanning themselves if they reference pattern checks
            if rel_path in ("tests/test_no_secrets.py", "tests/fixtures/test_ocr_run.jpg"):
                continue

            if not os.path.exists(rel_path) or not os.path.isfile(rel_path):
                continue

            try:
                with open(rel_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue

            # Check for Telegram bot tokens
            for match in self.TELEGRAM_TOKEN_REGEX.finditer(content):
                token = match.group(0)
                if token not in self.ALLOWED_TEST_TOKENS:
                    violations.append(f"{rel_path}: Leaked Telegram token pattern found ({token[:6]}...)")

            # Check for Google AIza API keys
            for match in self.AIZA_KEY_REGEX.finditer(content):
                key = match.group(0)
                if key not in self.ALLOWED_TEST_TOKENS:
                    violations.append(f"{rel_path}: Leaked AIza key pattern found ({key[:6]}...)")

            # Check for Private keys
            if self.PRIVATE_KEY_REGEX.search(content):
                violations.append(f"{rel_path}: Private key block found")

        self.assertEqual(
            violations,
            [],
            f"Sensitive credentials detected in tracked files:\n" + "\n".join(violations)
        )

    def test_log_filter_masks_telegram_token(self):
        """Verifies that SensitiveDataFilter masks Telegram bot tokens in log messages and args."""
        log_filter = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Sending request to bot with token 8863268724:AAFcDfpdgTXas2E6OnNIQj9mRIwRzQ8WV94 now",
            args=(),
            exc_info=None
        )
        self.assertTrue(log_filter.filter(record))
        self.assertNotIn("8863268724:AAFcDfpdgTXas2E6OnNIQj9mRIwRzQ8WV94", record.msg)
        self.assertIn("[REDACTED_TELEGRAM_TOKEN]", record.msg)

    def test_log_filter_masks_aiza_key(self):
        """Verifies that SensitiveDataFilter masks AIza keys in log messages and args."""
        log_filter = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=20,
            msg="Gemini key is %s",
            args=("AIzaSyBxSq2mRHzoayVdItKrQqTC-4UIGqXiU8E",),
            exc_info=None
        )
        self.assertTrue(log_filter.filter(record))
        self.assertNotIn("AIzaSyBxSq2mRHzoayVdItKrQqTC-4UIGqXiU8E", str(record.args))
        self.assertIn("[REDACTED_API_KEY]", str(record.args))

    def test_get_effective_gemini_api_key_rejects_disabled(self):
        """Verifies that DISABLED, NONE, and NULL are recognized as no key."""
        from unittest.mock import patch
        for val in ["DISABLED", "NONE", "NULL", "none", "null", "disabled"]:
            with patch('ocr.gemini_vision.GEMINI_API_KEY', val):
                self.assertEqual(get_effective_gemini_api_key(), "")

if __name__ == '__main__':
    unittest.main()
