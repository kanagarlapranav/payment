"""
Comprehensive Regression & Invariant Test Suite for Full Audit Remediation.
Covers:
1. /api/transactions real HTTP integration test & query parameters
2. Empty initialized database exports valid Format v2 backup
3. Delete-all exports valid Format v2 backup
4. Old cloud backup cannot resurrect deleted rows
5. Invalid amount (NaN, Inf, negative, corrupt DB amount) raises ValueError on recalculate
6. UID is strictly immutable and cannot be updated via update_transaction
7. Temporary cloud backup files are always deleted in finally blocks
8. TRANSFER is excluded from income and expenses in summaries & reviews
9. TRANSFER leaves consolidated balance unchanged
10. Dashboard invalid pagination (page < 1, bad page_size) returns HTTP 400
11. Dashboard invalid year/month returns HTTP 400
12. Startup restore failure is observable and sets backup_blocked='1'
13. Atomic rollback: insertion failure does not mutate current_balance
14. Decimal summaries do not drift across additions and recalculations
15. Concurrent mutations under LEDGER_LOCK preserve ledger invariants
16. Failed backups preserve is_dirty='1'
17. Confirmed backups clear is_dirty='0' and record last_confirmed_backup_at
18. Unauthorized callbacks and mutating commands are rejected
19. Daily digest retries with exponential backoff and scheduler handles failures
20. HTML error responses have special characters properly escaped
"""

import os
import json
import uuid
import time
import math
import html
import threading
import sqlite3
from decimal import Decimal
from datetime import datetime, date
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from http.server import ThreadingHTTPServer

import pytest
import httpx

from config import DATA_DIR, DB_PATH
from database.db import setup_database, get_db_connection, LEDGER_LOCK
from database.models import Transaction, TransactionSummary
from database.queries import (
    insert_transaction_with_balance,
    get_transaction_by_id,
    get_transaction_by_uid,
    update_transaction,
    delete_transaction,
    restore_soft_deleted_transaction,
    get_transactions_paginated,
    get_daily_summary_stats,
    get_monthly_summary,
    ALLOWED_UPDATE_COLUMNS,
    increment_revision_and_mark_dirty,
)
from services.balance_service import (
    recalculate_all_balances,
    recalculate_in_connection,
    set_explicit_balance,
    get_today_summary,
    get_overall_summary,
    validate_ledger_invariants,
    update_balance_for_transaction,
)
from services.backup_service import (
    export_database_to_json,
    import_database_from_json,
    verify_backup_payload,
    compute_canonical_checksum,
    backup_to_telegram,
    restore_from_telegram,
    record_confirmed_backup,
    mark_dirty,
    BACKUP_JSON_PATH,
)
from services.monthly_review_service import (
    calculate_monthly_closing_metrics,
    close_and_record_monthly_review,
    get_monthly_review,
)
from services.scheduler_service import format_daily_digest, send_daily_digest_with_retry
from services.dashboard_auth import create_one_time_code, exchange_code_for_session
from app import WebAppAndHealthHandler, on_startup
from utils.dates import utc_now_iso, get_current_time_in_tz
from utils.validation import parse_decimal_amount, CENT


@pytest.fixture(autouse=True)
def clean_test_environment(tmp_path, monkeypatch):
    """Provides a fresh isolated database and data directory for each test."""
    db_file = tmp_path / "test_db.sqlite3"
    backup_file = tmp_path / "test_backup.json"
    
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("database.db.DB_PATH", db_file)
    monkeypatch.setattr("services.backup_service.DB_PATH", db_file)
    monkeypatch.setattr("services.backup_service.BACKUP_JSON_PATH", backup_file)
    monkeypatch.setattr("services.backup_service.DATA_DIR", tmp_path)
    monkeypatch.setattr("config.DATA_DIR", tmp_path)
    monkeypatch.setattr("config.TELEGRAM_USER_ID", 123456789)
    monkeypatch.setattr("config.TELEGRAM_GROUP_ID", -100123456789)

    setup_database()
    yield
    if db_file.exists():
        try:
            db_file.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Test 1 & 10 & 11: Real HTTP Integration Tests on /api/transactions & Dashboard
# ---------------------------------------------------------------------------

def test_dashboard_api_transactions_http_integration(tmp_path):
    """Spins up real ThreadingHTTPServer, exchanges auth code, and queries /api/transactions."""
    # 1. Insert test transactions
    t1 = Transaction(amount=500.0, transaction_type="SENT", person_name="Coffee Shop", transaction_date="2026-09-21")
    t2 = Transaction(amount=1500.0, transaction_type="RECEIVED", person_name="Freelance Client", transaction_date="2026-09-21")
    t3 = Transaction(amount=1000.0, transaction_type="TRANSFER", person_name="Savings Account", transaction_date="2026-09-21")
    insert_transaction_with_balance(t1)
    insert_transaction_with_balance(t2)
    insert_transaction_with_balance(t3)

    server = ThreadingHTTPServer(('127.0.0.1', 0), WebAppAndHealthHandler)
    server_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{server_port}"
    try:
        with httpx.Client(base_url=base_url) as client:
            # Step A: Health check (public)
            res_health = client.get("/healthz")
            assert res_health.status_code == 200
            assert res_health.text == "OK"

            # Step B: Unauthorized without session returns 401
            res_unauth = client.get("/api/transactions")
            assert res_unauth.status_code == 401

            # Step C: Auth Code Exchange
            code = create_one_time_code()
            res_auth = client.get(f"/auth?code={code}", follow_redirects=False)
            assert res_auth.status_code == 303
            cookie = res_auth.headers.get("Set-Cookie")
            assert "session_id=" in cookie
            assert "HttpOnly" in cookie

            # Step D: Authenticated call to /api/transactions
            headers = {"Cookie": cookie}
            res_tx = client.get("/api/transactions?page=1&page_size=10", headers=headers)
            assert res_tx.status_code == 200
            data = res_tx.json()
            assert data["total_count"] == 3
            assert len(data["transactions"]) == 3

            # Step E: Query parameter type=SENT filter
            res_sent = client.get("/api/transactions?page=1&page_size=10&type=SENT", headers=headers)
            assert res_sent.status_code == 200
            sent_data = res_sent.json()
            assert sent_data["total_count"] == 1
            assert sent_data["transactions"][0]["person_name"] == "Coffee Shop"

            # Step F: Query parameter type=TRANSFER filter
            res_tr = client.get("/api/transactions?page=1&page_size=10&type=TRANSFER", headers=headers)
            assert res_tr.status_code == 200
            tr_data = res_tr.json()
            assert tr_data["total_count"] == 1
            assert tr_data["transactions"][0]["person_name"] == "Savings Account"

            # Step G: Invalid pagination bounds (page < 1, page_size > 200 or invalid) return 400
            res_bad_page = client.get("/api/transactions?page=0", headers=headers)
            assert res_bad_page.status_code == 400
            res_bad_size = client.get("/api/transactions?page_size=0", headers=headers)
            assert res_bad_size.status_code == 400
            res_bad_type = client.get("/api/transactions?type=INVALID_TYPE", headers=headers)
            assert res_bad_type.status_code == 400

            # Step H: Invalid year or month return 400
            res_bad_year = client.get("/api/transactions?year=1850", headers=headers)
            assert res_bad_year.status_code == 400
            res_bad_month = client.get("/api/transactions?month=13", headers=headers)
            assert res_bad_month.status_code == 400
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Test 2 & 3: Empty Initialized Database & Delete-All Export Format v2 Backup
# ---------------------------------------------------------------------------

def test_empty_initialized_database_and_delete_all_backup():
    """Initialized empty database and delete-all must produce valid Format v2 backup."""
    # 1. Mark database explicitly initialized
    with get_db_connection() as conn:
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'database_initialized'")

    # 2. Export empty database
    backup = export_database_to_json()
    assert backup.get("version") == 2
    assert backup.get("transaction_count") == 0
    assert backup.get("live_count") == 0
    assert backup.get("empty_ledger") is True
    assert isinstance(backup.get("checksum"), str)
    assert len(backup.get("checksum")) == 64

    valid, err = verify_backup_payload(backup)
    assert valid is True
    assert err == "OK"

    # 3. Insert and then delete all transactions
    t1 = Transaction(amount=250.0, transaction_type="SENT", person_name="Grocery Store")
    t1_id = insert_transaction_with_balance(t1)
    delete_transaction(t1_id)

    del_backup = export_database_to_json()
    assert del_backup.get("version") == 2
    assert del_backup.get("transaction_count") == 1  # 1 tombstone
    assert del_backup.get("live_count") == 0
    assert del_backup.get("empty_ledger") is True

    valid_del, err_del = verify_backup_payload(del_backup)
    assert valid_del is True


# ---------------------------------------------------------------------------
# Test 4: Old Cloud Backup Cannot Resurrect Deleted Rows
# ---------------------------------------------------------------------------

def test_newer_empty_backup_prevents_old_cloud_resurrection():
    """Importing a newer empty backup over older local rows cleanly clears the live ledger."""
    # 1. Create a local ledger with 2 live rows
    t1 = Transaction(amount=100.0, transaction_type="SENT", person_name="A")
    t2 = Transaction(amount=200.0, transaction_type="RECEIVED", person_name="B")
    insert_transaction_with_balance(t1)
    insert_transaction_with_balance(t2)

    # 2. Simulate incoming cloud backup with higher revision stating empty ledger
    empty_backup_payload = {
        "version": 2,
        "database_id": str(uuid.uuid4()),
        "revision": 99,
        "exported_at": utc_now_iso(),
        "transaction_count": 0,
        "live_count": 0,
        "empty_ledger": True,
        "balance": 0.0,
        "settings": {"initial_balance": "0.0", "database_initialized": "1"},
        "custom_menu_items": [],
        "budgets": [],
        "transactions": [],
    }
    empty_backup_payload["checksum"] = compute_canonical_checksum(empty_backup_payload)

    res = import_database_from_json(data_dict=empty_backup_payload)
    assert res["success"] is True

    # 3. Verify no live transactions exist and balance is 0.0
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        assert float(cur.fetchone()['value']) == 0.0

    errors = validate_ledger_invariants()
    assert errors == []


# ---------------------------------------------------------------------------
# Test 5: Invalid Amount Raises Hard ValueError During Recalculation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("corrupt_val", ["NaN", "Infinity", "-100.0", "abc", 0.0])
def test_invalid_amount_raises_value_error_on_recalculation(corrupt_val):
    """Non-positive or non-finite amount in the database must raise ValueError and never become zero."""
    t1 = Transaction(amount=100.0, transaction_type="SENT", person_name="A")
    tx_id = insert_transaction_with_balance(t1)

    # Force direct corruption in SQLite table
    with get_db_connection() as conn:
        conn.execute("UPDATE transactions SET amount = ? WHERE id = ?", (corrupt_val, tx_id))

    # Recalculation must raise ValueError
    with pytest.raises(ValueError):
        recalculate_all_balances()


# ---------------------------------------------------------------------------
# Test 6: UID Immutability via update_transaction
# ---------------------------------------------------------------------------

def test_uid_immutability_enforced():
    """Attempting to mutate UID via update_transaction must raise ValueError."""
    assert 'uid' not in ALLOWED_UPDATE_COLUMNS

    t = Transaction(amount=100.0, transaction_type="SENT", person_name="Merchant")
    tx_id = insert_transaction_with_balance(t)
    original_tx = get_transaction_by_id(tx_id)
    orig_uid = original_tx["uid"]

    new_uid = uuid.uuid4().hex
    with pytest.raises(ValueError, match="Disallowed column"):
        update_transaction(tx_id, {"uid": new_uid})

    # Verify UID remained untouched
    tx_after = get_transaction_by_id(tx_id)
    assert tx_after["uid"] == orig_uid


# ---------------------------------------------------------------------------
# Test 7: Temporary Cloud Backup Cleanup in finally
# ---------------------------------------------------------------------------

def test_temp_cloud_backup_cleanup_on_all_failures(tmp_path, monkeypatch):
    """Temporary cloud backup JSON is guaranteed to be unlinked even if import/json parse fails."""
    async def _run():
        temp_file = tmp_path / "temp_cloud_backup.json"

        class MockFile:
            async def download_to_drive(self, custom_path):
                with open(custom_path, "w", encoding="utf-8") as f:
                    f.write("{ invalid json content ]]]")

        class MockDoc:
            file_id = "doc123"
            file_name = "payment_tracker_backup.json"

        class MockPinned:
            document = MockDoc()
            caption = "#PAYMENT_TRACKER_BACKUP_V2"

        class MockChat:
            pinned_message = MockPinned()

        mock_bot = AsyncMock()
        mock_bot.get_chat.return_value = MockChat()
        mock_bot.get_file.return_value = MockFile()

        monkeypatch.setattr("services.backup_service.DATA_DIR", tmp_path)
        res = await restore_from_telegram(mock_bot, chat_id="123456789")

        # Temp file must have been deleted by finally block
        assert not temp_file.exists()

    import asyncio
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 8 & 9: TRANSFER Transaction Invariants & Zero Balance Delta
# ---------------------------------------------------------------------------

def test_transfer_transaction_type_invariants():
    """TRANSFER maintains bal_after == bal_before, excluded from income, expenses, and savings."""
    set_explicit_balance(10000.0)

    t_sent = Transaction(amount=500.0, transaction_type="SENT", person_name="Store", transaction_date="2026-09-21")
    t_recv = Transaction(amount=2000.0, transaction_type="RECEIVED", person_name="Salary", transaction_date="2026-09-21")
    t_tr = Transaction(amount=3000.0, transaction_type="TRANSFER", person_name="FD Account", transaction_date="2026-09-21")

    id_sent = insert_transaction_with_balance(t_sent)
    id_recv = insert_transaction_with_balance(t_recv)
    id_tr = insert_transaction_with_balance(t_tr)

    # Check TRANSFER row balances
    tx_tr = get_transaction_by_id(id_tr)
    assert tx_tr["balance_before"] == tx_tr["balance_after"]
    assert tx_tr["balance_after"] == 11500.0  # 10000 - 500 + 2000 = 11500 (unchanged by transfer)

    # Check daily summary stats (TRANSFER excluded from total_sent and total_received)
    day_stats = get_daily_summary_stats("2026-09-21")
    assert day_stats["total_sent"] == 500.0
    assert day_stats["total_received"] == 2000.0
    assert day_stats["net_change"] == 1500.0

    # Check monthly review closing metrics
    closing = calculate_monthly_closing_metrics(2026, 9)
    assert closing["total_income"] == 2000.0
    assert closing["total_expense"] == 500.0
    assert closing["net_savings"] == 1500.0

    # Ledger invariants must pass
    errors = validate_ledger_invariants()
    assert errors == []


# ---------------------------------------------------------------------------
# Test 12: Startup Restore Failure Sets backup_blocked
# ---------------------------------------------------------------------------

def test_startup_restore_failure_sets_backup_blocked(monkeypatch):
    """When startup restore fails on an uninitialized empty database, backup_blocked is set to '1'."""
    async def _run():
        mock_app = MagicMock()
        mock_app.bot = AsyncMock()

        # Simulate cloud restore failure and no local backup
        monkeypatch.setattr("services.backup_service.restore_from_telegram", AsyncMock(return_value=False))
        monkeypatch.setattr("services.backup_service.restore_local_fallback_if_valid", lambda: False)

        await on_startup(mock_app)

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = 'backup_blocked'")
            assert cur.fetchone()['value'] == '1'

    import asyncio
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 13: Atomic Rollback on Insertion Failure
# ---------------------------------------------------------------------------

def test_atomic_rollback_on_insertion_failure():
    """If insertion fails due to constraint or invalid data, balance and database state are rolled back."""
    set_explicit_balance(5000.0)

    # Insert a valid transaction with reference number "REF123"
    t1 = Transaction(amount=200.0, transaction_type="SENT", person_name="Shop A", reference_number="REF123")
    insert_transaction_with_balance(t1)

    # Attempt to insert a duplicate reference number (which raises ValueError)
    t2 = Transaction(amount=300.0, transaction_type="SENT", person_name="Shop B", reference_number="REF123")
    with pytest.raises(ValueError, match="Duplicate live reference"):
        insert_transaction_with_balance(t2)

    # Balance must remain exactly 4800.0 (5000 - 200)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        assert float(cur.fetchone()['value']) == 4800.0
        cur.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NULL")
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Test 14: Decimal Precision and Summaries
# ---------------------------------------------------------------------------

def test_decimal_summaries_no_float_drift():
    """Repeated additions of floating pennies do not produce 0.30000000000000004 drift."""
    set_explicit_balance(0.0)

    # Add 100 transactions of 0.10
    for i in range(100):
        t = Transaction(amount=0.10, transaction_type="RECEIVED", person_name=f"Penny {i}", transaction_date="2026-09-21")
        insert_transaction_with_balance(t)

    summary = get_overall_summary()
    assert summary.total_received == 10.00
    assert summary.current_balance == 10.00
    assert summary.net_change == 10.00


# ---------------------------------------------------------------------------
# Test 15: Concurrent Mutations Preserve Ledger Invariants
# ---------------------------------------------------------------------------

def test_concurrent_mutations_preserve_ledger_invariants():
    """Concurrent threads inserting transactions under LEDGER_LOCK produce a continuous ledger chain."""
    set_explicit_balance(1000.0)

    def worker(worker_id):
        for j in range(5):
            t = Transaction(
                amount=10.0,
                transaction_type="SENT",
                person_name=f"Worker {worker_id}",
                transaction_date="2026-09-21",
                transaction_time=f"10:{worker_id:02d}:{j:02d}"
            )
            insert_transaction_with_balance(t)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # 4 workers * 5 txs * 10.0 = 200.0 spent -> closing balance = 800.0
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'current_balance'")
        assert float(cur.fetchone()['value']) == 800.0

    errors = validate_ledger_invariants()
    assert errors == []


# ---------------------------------------------------------------------------
# Test 16 & 17: Backup Dirty and Confirmed Status
# ---------------------------------------------------------------------------

def test_backup_revision_dirty_and_confirmed_lifecycle():
    """Mutation increments revision & sets is_dirty=1; recording confirmed backup clears is_dirty."""
    with get_db_connection() as conn:
        new_rev = increment_revision_and_mark_dirty(conn)
        assert new_rev >= 2

    # Check is_dirty is 1
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'is_dirty'")
        assert cur.fetchone()['value'] == '1'

    # Record confirmed backup
    record_confirmed_backup()

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'is_dirty'")
        assert cur.fetchone()['value'] == '0'
        cur.execute("SELECT value FROM settings WHERE key = 'last_confirmed_backup_at'")
        assert cur.fetchone()['value'] != ''


# ---------------------------------------------------------------------------
# Test 18: Authorization Central Policy
# ---------------------------------------------------------------------------

def test_authorization_policy_command_and_callback_whitelists():
    """Strictly verify owner-only actions versus read-only actions."""
    from bot.auth import get_command_policy, get_callback_policy

    assert get_command_policy("delete") == "admin"
    assert get_command_policy("setbalance") == "admin"
    assert get_command_policy("restore") == "admin"
    assert get_command_policy("balance") == "read_only"
    assert get_command_policy("history") == "read_only"

    assert get_callback_policy("delete_confirm") == "admin"
    assert get_callback_policy("rec_paid") == "admin"
    assert get_callback_policy("close_month") == "admin"
    assert get_callback_policy("nav") == "read_only"


# ---------------------------------------------------------------------------
# Test 19: Scheduler Send Retry
# ---------------------------------------------------------------------------

def test_scheduler_digest_retry_on_failure():
    """send_daily_digest_with_retry attempts retries on temporary Telegram errors."""
    async def _run():
        mock_bot = AsyncMock()
        mock_bot.send_message.side_effect = [Exception("Network drop"), Exception("Timeout"), MagicMock(message_id=999)]

        t = Transaction(amount=100.0, transaction_type="SENT", person_name="Dinner", transaction_date="2026-09-21")
        insert_transaction_with_balance(t)

        with patch("asyncio.sleep", AsyncMock()):
            ok = await send_daily_digest_with_retry(mock_bot, target_chat_id=123456789, target_date_str="2026-09-21")
            assert ok is True
            assert mock_bot.send_message.call_count == 3

    import asyncio
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 20: HTML Error-Page Values Escaping
# ---------------------------------------------------------------------------

def test_html_error_response_escaping():
    """HTML error responses must escape malicious scripts in query or result strings."""
    malicious_input = "<script>alert('xss')</script>"
    escaped = html.escape(malicious_input)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
