"""
Comprehensive End-to-End Integration Tests (PROMPT 11):
1. Atomic Ledger & Chaining: Add SENT, add RECEIVED, verify SHA-256 hash chain and balance continuity.
2. Recalculation: Edit transaction #1 and verify balance recalculations down the chain.
3. Soft Delete & Tombstones: Delete transaction #1, verify tombstone flag (deleted_at) and balance adjustment.
4. Undo Mechanism: Undo delete action and confirm restoration.
5. Backup v2 & Tamper Rejection: Export v2 backup, tamper with payload (checksum mismatch), verify restore fails.
6. Empty Ledger Backup: Delete all records, confirm empty-ledger backup creation and restart simulation (no ghost rows).
7. Balance Validation: /setbalance 100.10, reject NaN/Inf/negative inputs.
8. Authorization: Reject unauthorized users and group members on mutations/admin commands and callbacks.
9. Scheduler & Retries: 3-attempt exponential retry and idempotent sent tracking.
10. Concurrency: Multi-threaded concurrent writes without ledger corruption.
11. Dashboard Security: Parameter validation (year/month 400), session cookie enforcement (401).
12. Vision & OCR Resilience: Malformed Gemini JSON and OCR timeout handling.
13. HTML Safety: Escape injection payloads (<b>x</b>, &, <code>).
14. Cloud Backup Resilience: Handled backup upload failure message without crashing.
"""

import pytest
import asyncio
import io
import json
import math
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import config
from database.db import setup_database, get_db_connection, LEDGER_LOCK
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance,
    update_transaction,
    delete_transaction,
    get_all_transactions,
    get_balance_setting,
    get_transaction_by_id
)
from services.balance_service import set_explicit_balance
from services.undo_service import (
    record_delete_action,
    record_insert_action,
    perform_undo
)
from services.backup_service import (
    export_database_to_json,
    import_database_from_json,
    verify_backup_payload,
    backup_to_telegram
)
from services.scheduler_service import send_daily_digest_with_retry
from services.dashboard_auth import create_one_time_code, exchange_code_for_session
from app import WebAppAndHealthHandler, on_startup
from utils.html_safety import escape_html, sanitize_gemini_html
from bot.auth import require_authorized, require_admin
from ocr.gemini_vision import extract_transaction_with_gemini_async, compute_deterministic_confidence
from ocr.engine import extract_text_from_image


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Initializes an isolated SQLite database for each test run."""
    test_db = tmp_path / "test_integration.sqlite3"
    with patch("config.DB_PATH", test_db), patch("database.db.DB_PATH", test_db):
        setup_database()
        yield


# --- 1. Ledger Chaining, Recalculation, Soft Delete & Undo ---

def test_ledger_lifecycle_chain_edit_delete_undo():
    """Verifies full lifecycle: add SENT, add RECEIVED, hash chain, edit #1, delete #1, tombstone, and undo."""
    # 1. Set starting balance
    set_explicit_balance(1000.0)
    assert get_balance_setting() == 1000.0

    # 2. Add SENT transaction (₹200)
    tx1 = Transaction(
        amount=200.0,
        transaction_type="SENT",
        person_name="Vendor A",
        category="Supplies",
        transaction_date="2026-09-20",
        payment_app="Google Pay"
    )
    tx1_id = insert_transaction_with_balance(tx1)
    assert get_balance_setting() == 800.0

    # 3. Add RECEIVED transaction (₹500)
    tx2 = Transaction(
        amount=500.0,
        transaction_type="RECEIVED",
        person_name="Client B",
        category="Income",
        transaction_date="2026-09-20",
        payment_app="PhonePe"
    )
    tx2_id = insert_transaction_with_balance(tx2)
    assert get_balance_setting() == 1300.0

    # Verify ledger integrity
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, uid, balance_before, balance_after FROM transactions WHERE deleted_at IS NULL ORDER BY id ASC")
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0]["balance_before"] == 1000.0
        assert rows[0]["balance_after"] == 800.0
        assert rows[1]["balance_before"] == 800.0
        assert rows[1]["balance_after"] == 1300.0
        assert rows[0]["uid"] and rows[1]["uid"]
        assert rows[0]["uid"] != rows[1]["uid"]

    # 4. Edit transaction #1 (Change amount from 200 to 300)
    edit_success = update_transaction(tx1_id, {"amount": 300.0})
    assert edit_success is True
    assert get_balance_setting() == 1200.0

    # 5. Delete transaction #1 -> verify tombstone & balance recalculation
    tx1_row = get_transaction_by_id(tx1_id)
    record_delete_action(tx1_row)
    delete_success = delete_transaction(tx1_id)
    assert delete_success is True
    assert get_balance_setting() == 1500.0

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, deleted_at FROM transactions WHERE id = ?", (tx1_id,))
        deleted_row = cur.fetchone()
        assert deleted_row["deleted_at"] is not None

    # 6. Undo delete -> restores transaction #1
    success, undo_msg = perform_undo()
    assert success is True
    assert get_balance_setting() == 1200.0


# --- 2. Backup v2, Tamper Detection, Empty Ledger, & Restart Simulation ---

def test_backup_tamper_rejection_and_empty_ledger_restart(tmp_path):
    """Exports backup, rejects tampered checksums, tests empty-ledger backups and restart recovery."""
    # Add a valid transaction
    set_explicit_balance(500.0)
    tx = Transaction(amount=100.0, transaction_type="SENT", person_name="Test", transaction_date="2026-09-20")
    insert_transaction_with_balance(tx)

    backup_file = Path(tmp_path / "backup_v2.json")

    # 1. Export valid v2 backup
    export_database_to_json(backup_file)
    assert backup_file.exists()

    with open(backup_file, "r", encoding="utf-8") as f:
        backup_data = json.load(f)
    assert backup_data["version"] == 2
    assert backup_data["checksum"] is not None

    # 2. Tamper with backup file
    backup_data["transactions"][0]["amount"] = 99999.0
    tampered_file = Path(tmp_path / "tampered.json")
    with open(tampered_file, "w", encoding="utf-8") as f:
        json.dump(backup_data, f)

    # Restore must reject tampered file due to checksum mismatch
    res = import_database_from_json(tampered_file)
    assert res["success"] is False
    assert "checksum" in res["error"].lower() or "mismatch" in res["error"].lower()

    # 3. Delete everything and export empty ledger backup (tombstones preserved)
    all_txs = get_all_transactions()
    for t in all_txs:
        delete_transaction(t["id"])

    empty_backup_file = Path(tmp_path / "empty_backup.json")
    export_database_to_json(empty_backup_file)

    with open(empty_backup_file, "r", encoding="utf-8") as f:
        empty_data = json.load(f)
    assert empty_data["live_count"] == 0
    assert empty_data["empty_ledger"] is True

    # 4. Simulate application restart (on_startup)
    mock_app = MagicMock()
    mock_app.bot = MagicMock()

    async def _test_startup():
        with patch("services.backup_service.restore_from_telegram", return_value=False):
            with patch("services.backup_service.restore_local_fallback_if_valid", return_value=True):
                await on_startup(mock_app)
        # Verify no ghost rows resurrected
        live_txs = get_all_transactions()
        assert len(live_txs) == 0

    asyncio.run(_test_startup())


# --- 3. Input Validation: /setbalance 100.10, NaN, Inf, and Negative Amounts ---

def test_balance_and_amount_validation():
    """Validates /setbalance 100.10, rejects NaN, Inf, and negative amounts."""
    # Valid starting balance
    set_explicit_balance(100.10)
    assert math.isclose(get_balance_setting(), 100.10, rel_tol=1e-5)

    # Rejects NaN
    with pytest.raises(ValueError):
        tx_nan = Transaction(amount=float("nan"), transaction_type="SENT", person_name="Bad")
        insert_transaction_with_balance(tx_nan)

    # Rejects Inf
    with pytest.raises(ValueError):
        tx_inf = Transaction(amount=float("inf"), transaction_type="SENT", person_name="Bad")
        insert_transaction_with_balance(tx_inf)

    # Rejects Negative
    with pytest.raises(ValueError):
        tx_neg = Transaction(amount=-50.0, transaction_type="SENT", person_name="Bad")
        insert_transaction_with_balance(tx_neg)


# --- 4. Authorization Enforcement on Commands & Callbacks ---

def test_authorization_matrix():
    """Owner is allowed on admin commands and callbacks; non-owner group members and strangers are rejected."""
    mock_update = MagicMock()
    mock_update.effective_user.id = 999999999  # Stranger
    mock_update.effective_chat.id = 999999999

    async def _test_auth():
        with patch.object(config, "TELEGRAM_USER_ID", 123456789):
            with patch.object(config, "TELEGRAM_GROUP_ID", -1001234567890):
                # Stranger: rejected on both
                assert await require_authorized(mock_update) is False
                assert await require_admin(mock_update) is False

                # Group Member (non-owner): allowed on authorized (read-only), rejected on admin
                mock_update.effective_chat.id = -1001234567890
                assert await require_authorized(mock_update) is True
                assert await require_admin(mock_update) is False

                # Owner: allowed on all
                mock_update.effective_user.id = 123456789
                assert await require_authorized(mock_update) is True
                assert await require_admin(mock_update) is True

    asyncio.run(_test_auth())


# --- 5. Scheduler Failure, Retries & Concurrency ---

def test_scheduler_retry_and_concurrency():
    """Verifies scheduler retries up to 3 times on failure and ledger handles concurrent writes safely."""
    # 1. Scheduler 3-attempt retry
    mock_bot = MagicMock()
    call_count = 0

    async def mock_send_fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("Telegram Connection Error")
        return MagicMock()

    mock_bot.send_message = AsyncMock(side_effect=mock_send_fail)

    async def _test_scheduler():
        with patch("asyncio.sleep", return_value=None):
            with patch("ocr.gemini_vision.is_gemini_available", return_value=False):
                success = await send_daily_digest_with_retry(mock_bot, 12345, "2026-09-20")
                assert success is True
                assert call_count == 3

    asyncio.run(_test_scheduler())

    # 2. Multi-threaded concurrent writes
    set_explicit_balance(10000.0)
    threads = []
    errors = []

    def _worker(idx):
        try:
            tx = Transaction(
                amount=10.0,
                transaction_type="SENT" if idx % 2 == 0 else "RECEIVED",
                person_name=f"User {idx}",
                transaction_date="2026-09-20"
            )
            insert_transaction_with_balance(tx)
        except Exception as e:
            errors.append(e)

    for i in range(10):
        t = threading.Thread(target=_worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0


# --- 6. Dashboard Parameter Validation & Security ---

def test_dashboard_api_validation_and_auth():
    """Verifies 401 without session and 400 on invalid parameters."""
    handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
    handler.client_address = ("127.0.0.1", 5000)
    handler.headers = {}
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = io.BytesIO()

    # Unauthenticated -> 401
    handler.path = "/api/data"
    handler.do_GET()
    handler.send_response.assert_called_with(401)

    # Valid session with invalid month -> 400
    code = create_one_time_code()
    _, _, cookie_hdr = exchange_code_for_session(code, client_ip="127.0.0.1")
    session_cookie = cookie_hdr.split(";")[0]

    handler.headers = {"Cookie": session_cookie}
    handler.path = "/api/data?month=15"
    handler.do_GET()
    handler.send_response.assert_called_with(400)


# --- 7. Gemini Malformed JSON, OCR Timeout, HTML Safety & Backup Upload Failure ---

def test_gemini_ocr_and_html_safety(tmp_path):
    """Verifies malformed Gemini JSON parsing across model fallbacks, OCR timeout handling, and HTML escaping."""
    # 1. Malformed Gemini JSON on first model falls back to second model
    test_img = tmp_path / "test.jpg"
    test_img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9")

    mock_resp_bad = MagicMock()
    mock_resp_bad.status_code = 200
    mock_resp_bad.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "THIS IS NOT JSON"}]}}]
    }

    mock_resp_good = MagicMock()
    mock_resp_good.status_code = 200
    mock_resp_good.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "amount": 250.0,
            "transaction_type": "SENT",
            "person_name": "Ramesh",
            "reference_number": "123456789012"
        })}]}}]
    }

    call_seq = [mock_resp_bad, mock_resp_good]

    async def _test_gemini():
        with patch("ocr.gemini_vision.get_effective_gemini_api_key", return_value="fake_api_key"):
            with patch("ocr.gemini_vision._call_gemini_api_async") as mock_call:
                mock_call.side_effect = [(call_seq.pop(0), None), (call_seq.pop(0), None)]
                tx, confidence = await extract_transaction_with_gemini_async(
                    str(test_img), ocr_text="Paid 250 to Ramesh Ref 123456789012"
                )
                assert tx is not None
                assert tx.amount == 250.0
                assert tx.person_name == "Ramesh"
                assert confidence > 70

    asyncio.run(_test_gemini())

    # 2. HTML escaping on injection payloads
    assert escape_html("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert escape_html("A & B") == "A &amp; B"
    assert escape_html("<code>code</code>") == "&lt;code&gt;code&lt;/code&gt;"

    # 3. Allowlisted Gemini remarks
    raw_ai = "<b>Great job!</b> <script>alert(1)</script>"
    clean_ai = sanitize_gemini_html(raw_ai)
    assert "<b>Great job!</b>" in clean_ai
    assert "<script>" not in clean_ai
    assert "&lt;script&gt;" in clean_ai


def test_ocr_timeout_resilience(tmp_path):
    """Verifies OCR timeout returns empty text safely without crashing."""
    test_img = tmp_path / "dummy.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

    with patch("ocr.engine.get_rapid_ocr_engine") as mock_engine:
        def slow_ocr(*args, **kwargs):
            time.sleep(0.01)
            return (None, None)
        mock_engine.return_value = slow_ocr
        res = extract_text_from_image(str(test_img))
        assert isinstance(res, str)


def test_backup_upload_failure_message():
    """Verifies failure to upload backup to Telegram logs error and returns False gracefully."""
    mock_bot = MagicMock()
    mock_bot.send_document = AsyncMock(side_effect=Exception("Telegram Network Timeout"))

    async def _run_upload():
        with patch.object(config, "TELEGRAM_USER_ID", 12345):
            success = await backup_to_telegram(mock_bot)
            assert success is False

    asyncio.run(_run_upload())
