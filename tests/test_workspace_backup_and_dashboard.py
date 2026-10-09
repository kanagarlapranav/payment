import os
import uuid
import json
import io
import csv
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from database.db import get_db_connection, setup_database
from database.models import Transaction
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    insert_transaction,
    get_all_transactions,
    get_all_transactions_asc,
    get_balance_setting,
    get_workspace_setting,
    set_workspace_setting,
)
from services.balance_service import recalculate_all_balances
from services.backup_service import (
    export_workspace_backup,
    import_database_from_json,
    verify_backup_payload,
    compute_canonical_checksum
)
from services.dashboard_auth import (
    create_one_time_code,
    exchange_code_for_session,
    validate_session_id,
    get_session_info,
    get_session_info_from_cookie
)
from app import WebAppAndHealthHandler


@pytest.fixture(autouse=True)
def init_db(tmp_path):
    setup_database()


def test_export_workspace_backup_isolation(tmp_path):
    """Verify that exporting a workspace backup includes only that workspace's records and valid checksum."""
    # Create Workspace 1 and Workspace 2
    ws1 = get_or_create_workspace(chat_id="111111", chat_type="private", title="Team One", creator_user_id=101)
    ws2 = get_or_create_workspace(chat_id="222222", chat_type="private", title="Team Two", creator_user_id=201)

    # Insert transactions into Workspace 1
    t1 = Transaction(amount=500.0, transaction_type="SENT", person_name="Supplier One", workspace_id=ws1.id)
    t2 = Transaction(amount=1500.0, transaction_type="RECEIVED", person_name="Client One", workspace_id=ws1.id)
    insert_transaction(t1)
    insert_transaction(t2)

    # Insert transaction into Workspace 2
    t3 = Transaction(amount=9999.0, transaction_type="SENT", person_name="Secret Vendor", workspace_id=ws2.id)
    insert_transaction(t3)

    # Export Workspace 1 backup
    out_file = tmp_path / "ws1_backup.json"
    backup = export_workspace_backup(ws1.id, output_path=out_file)

    assert backup is not None
    assert backup.get("format_version") == "workspace_v1"
    assert backup.get("workspace_id") == ws1.id
    assert backup.get("transaction_count") == 2
    
    payees = [tx['person_name'] for tx in backup.get("transactions", [])]
    assert "Supplier One" in payees
    assert "Client One" in payees
    assert "Secret Vendor" not in payees

    # Verify checksum validity
    valid, err = verify_backup_payload(backup)
    assert valid is True
    assert err == "OK"


def test_import_database_scoped_to_target_workspace(tmp_path):
    """Verify that importing a backup into target_workspace_id only mutates target workspace data."""
    ws_target = get_or_create_workspace(chat_id="333333", chat_type="group", title="Target Team", creator_user_id=301)
    ws_other = get_or_create_workspace(chat_id="444444", chat_type="group", title="Untouched Team", creator_user_id=401)

    # Insert transaction into untouched team
    t_other = Transaction(amount=777.0, transaction_type="RECEIVED", person_name="Untouched Payee", workspace_id=ws_other.id)
    insert_transaction(t_other)

    # Prepare JSON backup payload to import
    tx_data = [
        {
            "uid": uuid.uuid4().hex,
            "transaction_type": "SENT",
            "amount": 250.0,
            "person_name": "New Target Payee",
            "category": "Food",
            "reference_number": "REF-RESTORE-1",
            "transaction_date": "2026-10-01",
            "transaction_time": "12:00:00"
        }
    ]
    payload = {
        "version": 2,
        "format_version": "workspace_v1",
        "workspace_id": ws_target.id,
        "revision": 2,
        "balance": -250.0,
        "settings": {"initial_balance": "0.0"},
        "custom_menu_items": [],
        "budgets": [],
        "transactions": tx_data
    }
    payload["checksum"] = compute_canonical_checksum(payload)

    # Import into target workspace
    result = import_database_from_json(data_dict=payload, target_workspace_id=ws_target.id)
    assert result['success'] is True
    assert result['inserted'] == 1

    # Check target workspace transactions
    target_txs = get_all_transactions(workspace_id=ws_target.id)
    assert any(tx['person_name'] == "New Target Payee" for tx in target_txs)

    # Check other workspace transactions (must be completely untouched)
    other_txs = get_all_transactions(workspace_id=ws_other.id)
    assert len(other_txs) == 1
    assert other_txs[0]['person_name'] == "Untouched Payee"


def test_dashboard_auth_and_session_workspace_scoping():
    """Verify that one-time auth code and session exchange preserve and expose workspace_id."""
    user_id = 12345678
    from database.queries import get_or_create_workspace
    ws = get_or_create_workspace(chat_id=user_id, chat_type="dm", title="Dash Workspace", creator_user_id=user_id)
    ws_id = ws.id

    # Generate one-time code for specific tenant
    code = create_one_time_code(user_id=user_id, workspace_id=ws_id)
    assert len(code) >= 32

    # Exchange code for session
    success, session_id, cookie_header = exchange_code_for_session(code, client_ip="127.0.0.1")
    assert success is True
    assert len(session_id) >= 32
    assert "session_id=" in cookie_header

    # Verify session lookup exposes workspace_id
    session_info = get_session_info(session_id)
    assert session_info is not None
    assert session_info.get("workspace_id") == ws_id
    assert session_info.get("user_id") == user_id

    # Verify cookie header extractor
    info_from_cookie = get_session_info_from_cookie(cookie_header)
    assert info_from_cookie is not None
    assert info_from_cookie.get("workspace_id") == ws_id


def test_dashboard_data_api_and_csv_export_workspace_isolation():
    """Verify that HTTP handler for /api/data and /api/export.csv strictly isolates by session workspace."""
    ws_alpha = get_or_create_workspace(chat_id="555555", chat_type="group", title="Alpha Corp", creator_user_id=501)
    ws_beta = get_or_create_workspace(chat_id="666666", chat_type="group", title="Beta LLC", creator_user_id=601)

    from datetime import datetime
    today = datetime.now().date()

    # Alpha transaction
    t_a = Transaction(amount=300.0, transaction_type="SENT", person_name="Alpha Vendor", workspace_id=ws_alpha.id, transaction_date=today)
    insert_transaction(t_a)

    # Beta transaction
    t_b = Transaction(amount=900.0, transaction_type="SENT", person_name="Beta Vendor", workspace_id=ws_beta.id, transaction_date=today)
    insert_transaction(t_b)

    # Create session for Alpha
    code_a = create_one_time_code(user_id=501, workspace_id=ws_alpha.id)
    _, sess_a, cookie_a = exchange_code_for_session(code_a, client_ip="127.0.0.1")

    # Helper mock to invoke WebAppAndHealthHandler.do_GET
    def simulate_request(path: str, cookie: str):
        handler = WebAppAndHealthHandler.__new__(WebAppAndHealthHandler)
        handler.path = path
        handler.headers = {'Cookie': cookie, 'X-Forwarded-For': '127.0.0.1'}
        handler.client_address = ('127.0.0.1', 54321)
        handler.wfile = io.BytesIO()
        handler._send_security_headers = MagicMock()
        handler.do_GET()
        return handler.wfile.getvalue().decode('utf-8')

    # 1. /api/data scoped to Alpha
    res_data_raw = simulate_request('/api/data', cookie_a)
    data_json = json.loads(res_data_raw)
    assert "recent_transactions" in data_json
    alpha_payees = [tx['person_name'] for tx in data_json['recent_transactions']]
    assert "Alpha Vendor" in alpha_payees
    assert "Beta Vendor" not in alpha_payees

    # 2. /api/export.csv scoped to Alpha
    res_csv = simulate_request('/api/export.csv', cookie_a)
    assert "Alpha Vendor" in res_csv
    assert "Beta Vendor" not in res_csv


def test_restore_workspace_from_json_validates_invariants():
    """Verify that restore_workspace_from_json recalculates and rejects corrupted invariants."""
    import hashlib
    from services.backup_service import restore_workspace_from_json
    ws = get_or_create_workspace(chat_id="998811", chat_type="private", title="Invariant Test", creator_user_id=101)
    
    # Payload with invalid UID format violating ledger invariants
    bad_payload = {
        "format_version": "workspace_v1",
        "workspace_id": ws.id,
        "exported_at": "2026-10-09T00:00:00Z",
        "transactions": [
            {
                "uid": "INVALID_NOT_HEX_UID",
                "transaction_type": "SENT",
                "amount": 100.0,
                "person_name": "Actor 1",
                "transaction_date": "2026-10-09",
                "reference_number": "REF-999"
            }
        ],
        "settings": []
    }
    canonical = json.dumps(bad_payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    bad_payload["checksum"] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    
    res = restore_workspace_from_json(ws.id, bad_payload)
    assert res["success"] is False
    assert "invariants" in res["error"].lower()

