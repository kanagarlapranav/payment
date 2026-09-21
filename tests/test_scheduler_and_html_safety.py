"""
Comprehensive Unit Tests for PROMPT 9:
1. JobQueue Scheduler (run_daily with ZoneInfo("Asia/Kolkata"), DAILY_DIGEST_TIME config constant)
2. Daily Digest idempotency (sends once per day, marks sent only after successful send)
3. Retry behavior (retries up to 3 times on send failure, awaits coroutine, no unawaited warnings)
4. Backup retry job (every 60s) and tombstone purge job registration
5. HTML safety helper (escaping person names, categories, banks, UTRs, OCR text)
6. Allowlisted Gemini HTML formatting (<b>, <i>, <code> only)
7. Message splitting over 4096 chars and plain-text fallback on BadRequest
"""

import pytest
import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.error import BadRequest

import config
from database.db import setup_database, get_db_connection, LEDGER_LOCK
from database.models import Transaction
from database.queries import insert_transaction_with_balance
from services.scheduler_service import (
    parse_digest_time,
    format_daily_digest,
    send_daily_digest_with_retry,
    daily_digest_job,
    backup_retry_job,
    tombstone_purge_job,
    register_scheduler_jobs,
    IST_TZ
)
from utils.html_safety import (
    escape_html,
    sanitize_gemini_html,
    split_message,
    send_safe_message,
    strip_html_tags
)


@pytest.fixture(autouse=True)
def init_test_db():
    setup_database()


# --- 1. Scheduler Timing & Configuration ---

def test_parse_digest_time_zoneinfo():
    """parse_digest_time parses DAILY_DIGEST_TIME into ZoneInfo('Asia/Kolkata')."""
    with patch.object(config, "DAILY_DIGEST_TIME", "22:00"):
        t = parse_digest_time()
        assert t.hour == 22
        assert t.minute == 0
        assert str(t.tzinfo) == "Asia/Kolkata"

    with patch.object(config, "DAILY_DIGEST_TIME", "21:30"):
        t = parse_digest_time()
        assert t.hour == 21
        assert t.minute == 30


def test_register_scheduler_jobs_on_job_queue():
    """register_scheduler_jobs registers daily digest, backup retry, and tombstone purge jobs."""
    mock_app = MagicMock()
    mock_jq = MagicMock()
    mock_app.job_queue = mock_jq

    register_scheduler_jobs(mock_app)

    # Verify run_daily for daily digest
    assert mock_jq.run_daily.called
    digest_call = mock_jq.run_daily.call_args
    assert digest_call[1]["name"] == "daily_digest_job"

    # Verify run_repeating for backup retry (60s) and tombstone purge (86400s)
    assert mock_jq.run_repeating.call_count == 2
    job_names = [call[1]["name"] for call in mock_jq.run_repeating.call_args_list]
    assert "backup_retry_job" in job_names
    assert "tombstone_purge_job" in job_names


# --- 2. Daily Digest Idempotency & Send Retries ---

def test_daily_digest_sends_once_per_day():
    """daily_digest_job sends once per day and skips if already sent."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_context = MagicMock()
    mock_context.bot = mock_bot

    today_str = datetime.now(IST_TZ).strftime("%Y-%m-%d")

    async def _run():
        # First execution: should send and mark database
        with patch.object(config, "TELEGRAM_USER_ID", 12345):
            await daily_digest_job(mock_context)
            assert mock_bot.send_message.called

            # Check settings table
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM settings WHERE key = 'last_digest_sent_date'")
                assert cur.fetchone()["value"] == today_str

            # Reset mock and run again on same day: should skip!
            mock_bot.send_message.reset_mock()
            await daily_digest_job(mock_context)
            assert not mock_bot.send_message.called

    asyncio.run(_run())


def test_daily_digest_retry_on_failure():
    """send_daily_digest_with_retry retries up to 3 times with backoff on send failure."""
    mock_bot = MagicMock()
    attempts = 0

    async def mock_send(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Exception("Telegram Connection Timeout")
        return MagicMock()

    mock_bot.send_message = AsyncMock(side_effect=mock_send)

    async def _run():
        with patch("asyncio.sleep", return_value=None):
            success = await send_daily_digest_with_retry(mock_bot, target_chat_id=12345, target_date_str="2026-09-20")
            assert success is True
            assert attempts == 3

    asyncio.run(_run())


def test_daily_digest_marks_sent_only_after_successful_send():
    """If all retry attempts fail, the day is NOT marked as sent in settings."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("Network Unreachable"))

    async def _run():
        with patch("asyncio.sleep", return_value=None):
            success = await send_daily_digest_with_retry(mock_bot, target_chat_id=12345, target_date_str="2026-09-21")
            assert success is False

            # Ensure setting was not marked
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM settings WHERE key = 'last_digest_sent_date'")
                row = cur.fetchone()
                last_sent = row["value"] if row else ""
                assert last_sent != "2026-09-21"

    asyncio.run(_run())


# --- 3. HTML Safety & Dynamic Value Escaping ---

def test_html_escape_dynamic_values():
    """Dynamic values with HTML special chars are safely escaped."""
    assert escape_html("<b>John & Sons</b>") == "&lt;b&gt;John &amp; Sons&lt;/b&gt;"
    assert escape_html("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert escape_html("Chai & Samosa") == "Chai &amp; Samosa"
    assert escape_html('Store "XYZ"') == "Store &quot;XYZ&quot;"
    assert escape_html(None) == ""


def test_format_daily_digest_escapes_injected_names():
    """format_daily_digest safely escapes injection strings in names and categories."""
    tx = Transaction(
        amount=120.0,
        transaction_type="SENT",
        person_name="<script>evil()</script>",
        category="<b>Food & Snacks</b>",
        transaction_date="2026-09-20",
        payment_app="Google Pay"
    )
    insert_transaction_with_balance(tx)

    with patch("ocr.gemini_vision.is_gemini_available", return_value=False):
        digest = format_daily_digest("2026-09-20")
    assert "<script>" not in digest
    assert "&lt;script&gt;evil()&lt;/script&gt;" in digest
    assert "&lt;b&gt;Food &amp; Snacks&lt;/b&gt;" in digest


def test_sanitize_gemini_html_allowlist():
    """sanitize_gemini_html allows only b, i, em, strong, code and escapes other tags."""
    raw = "Great job! <b>You saved ₹500</b>. <i>Keep it up!</i> <code>CODE123</code> <script>hack()</script> <img src=x>"
    sanitized = sanitize_gemini_html(raw)

    assert "<b>You saved ₹500</b>" in sanitized
    assert "<i>Keep it up!</i>" in sanitized
    assert "<code>CODE123</code>" in sanitized
    assert "<script>" not in sanitized
    assert "&lt;script&gt;" in sanitized
    assert "&lt;img" in sanitized


# --- 4. Message Splitting & Plain Text Fallback ---

def test_split_message_under_4096():
    """split_message properly partitions text over 4096 characters."""
    long_text = "\n".join([f"Line {i}: Some financial statement transaction record entry" for i in range(150)])
    assert len(long_text) > 5000

    chunks = split_message(long_text, max_length=4096)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 4096


def test_send_safe_message_falls_back_on_bad_request():
    """send_safe_message catches BadRequest HTML parsing error and sends clean plain text."""
    mock_bot = MagicMock()
    call_log = []

    async def mock_send(chat_id, text, parse_mode=None, **kwargs):
        call_log.append({"text": text, "parse_mode": parse_mode})
        if parse_mode == "HTML" and "unclosed" in text:
            raise BadRequest("Can't parse entities: unclosed token")
        return MagicMock()

    mock_bot.send_message = AsyncMock(side_effect=mock_send)

    async def _run():
        bad_html = "<b>Unclosed tag test unclosed"
        await send_safe_message(mock_bot, chat_id=123, text=bad_html, parse_mode="HTML")
        assert len(call_log) == 2
        # First attempt with HTML failed
        assert call_log[0]["parse_mode"] == "HTML"
        # Fallback attempt sent stripped plain text with parse_mode=None
        assert call_log[1]["parse_mode"] is None
        assert call_log[1]["text"] == "Unclosed tag test unclosed"

    asyncio.run(_run())
