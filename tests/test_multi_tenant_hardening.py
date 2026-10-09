"""
Unit and Integration Tests for Multi-Tenant Hardening (Milestones 2 to 14):
1. Workspace Invites (Creation, hashing, expiry, max uses, redemption)
2. Workspace-Scoped Audit Logs (Logging, details JSON, retrieval)
3. Persistent Dashboard Auth (Codes, sessions, role changes, revocation)
4. Tenant-Safe Backup & Restore (Exports, checksum verification, cross-tenant rejection)
5. Scheduler Deduplication (Claiming, completion, duplicate execution blocking)
6. Scoped Undo Records (Isolation between workspaces)
"""

import uuid
import time
import pytest
from database.db import get_db_connection, LEDGER_LOCK
from database.queries import (
    get_or_create_workspace, add_workspace_member, get_workspace_member,
    insert_transaction, get_all_transactions, update_workspace_member_role
)
from services.invite_service import (
    create_workspace_invite, validate_and_redeem_invite, revoke_workspace_invite
)
from services.audit_service import (
    log_audit_event, get_workspace_audit_logs
)
from services.dashboard_auth import (
    create_one_time_code, exchange_code_for_session, get_session_info,
    revoke_session, validate_dashboard_action
)
from services.backup_service import (
    export_workspace_to_json, restore_workspace_from_json
)
from services.scheduler_service import (
    claim_workspace_job, complete_workspace_job
)
from services.undo_service import (
    record_delete_action, perform_undo, get_last_action
)


@pytest.fixture
def test_workspaces():
    """Sets up two clean test workspaces with unique chat IDs."""
    import random
    ws_a_id = f"test_ws_a_{uuid.uuid4().hex[:8]}"
    ws_b_id = f"test_ws_b_{uuid.uuid4().hex[:8]}"
    chat_a_id = random.randint(100000, 499999)
    chat_b_id = random.randint(500000, 999999)
    
    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        now = "2026-10-07T00:00:00"
        cursor.execute("INSERT INTO workspaces (id, chat_id, chat_type, title, is_active, status, created_at, updated_at) VALUES (?, ?, 'group', 'Alpha Org', 1, 'active', ?, ?)", (ws_a_id, chat_a_id, now, now))
        cursor.execute("INSERT INTO workspaces (id, chat_id, chat_type, title, is_active, status, created_at, updated_at) VALUES (?, ?, 'group', 'Beta Org', 1, 'active', ?, ?)", (ws_b_id, chat_b_id, now, now))
        
        # Add owners
        cursor.execute("INSERT INTO workspace_members (workspace_id, telegram_user_id, username, display_name, role, is_active, status, joined_at, updated_at) VALUES (?, 5001, 'alpha_owner', 'Alice', 'owner', 1, 'active', ?, ?)", (ws_a_id, now, now))
        cursor.execute("INSERT INTO workspace_members (workspace_id, telegram_user_id, username, display_name, role, is_active, status, joined_at, updated_at) VALUES (?, 5002, 'beta_owner', 'Bob', 'owner', 1, 'active', ?, ?)", (ws_b_id, now, now))
        conn.commit()

    return ws_a_id, ws_b_id



def test_invite_lifecycle(test_workspaces):
    ws_a_id, _ = test_workspaces
    
    # 1. Create invite
    raw_token, invite_id = create_workspace_invite(
        workspace_id=ws_a_id,
        creator_user_id=5001,
        intended_role="member",
        max_uses=1,
        expiry_hours=24
    )
    assert raw_token is not None
    assert invite_id is not None
    assert len(raw_token) > 20

    # 2. Redeem invite for a new user
    new_user_id = 9991
    success, msg, redeemed_ws, role = validate_and_redeem_invite(
        raw_token=raw_token,
        user_id=new_user_id,
        username="new_joiner",
        display_name="New Member"
    )
    assert success is True
    assert redeemed_ws == ws_a_id
    assert role == "member"

    # Verify user exists in workspace_members
    mem = get_workspace_member(ws_a_id, new_user_id)
    assert mem is not None
    assert mem.role == "member"
    assert mem.is_active == 1

    # 3. Re-use should fail (max_uses = 1)
    success2, msg2, _, _ = validate_and_redeem_invite(
        raw_token=raw_token,
        user_id=9992,
        username="second_joiner"
    )
    assert success2 is False
    assert "maximum usage limit" in msg2.lower()


def test_invite_revocation(test_workspaces):
    ws_a_id, _ = test_workspaces
    raw_token, invite_id = create_workspace_invite(
        workspace_id=ws_a_id,
        creator_user_id=5001,
        intended_role="admin",
        max_uses=5
    )
    
    # Revoke it
    revoked = revoke_workspace_invite(ws_a_id, invite_id, revoked_by=5001)
    assert revoked is True

    # Try to redeem
    success, msg, _, _ = validate_and_redeem_invite(raw_token, user_id=9993)
    assert success is False
    assert "revoked" in msg.lower()


def test_workspace_audit_logging(test_workspaces):
    ws_a_id, _ = test_workspaces
    
    # Log an event
    logged = log_audit_event(
        workspace_id=ws_a_id,
        actor_user_id=5001,
        action="test_security_event",
        resource="config:test",
        actor_role="owner",
        details={"ip": "127.0.0.1", "action_code": 42}
    )
    assert logged is True

    # Query audit logs
    events = get_workspace_audit_logs(ws_a_id, limit=10)
    assert len(events) >= 1
    recent = events[0]
    assert recent['action'] == "test_security_event"
    assert recent['actor_user_id'] == 5001
    assert recent['details']['action_code'] == 42


def test_persistent_dashboard_auth(test_workspaces):
    ws_a_id, _ = test_workspaces
    
    # Generate one-time code
    code = create_one_time_code(user_id=5001, workspace_id=ws_a_id, role="owner")
    assert code is not None

    # Exchange for session
    ok, session_id, cookie_hdr = exchange_code_for_session(code, client_ip="127.0.0.1")
    assert ok is True
    assert session_id is not None
    assert "HttpOnly" in cookie_hdr

    # Verify session lookup
    sinfo = get_session_info(session_id)
    assert sinfo is not None
    assert sinfo['user_id'] == 5001
    assert sinfo['workspace_id'] == ws_a_id
    assert sinfo['role'] == "owner"

    # Verify CSRF validation
    csrf_token = sinfo['csrf_token']
    val_ok, _ = validate_dashboard_action(sinfo, required_role="owner", csrf_token_header=csrf_token, is_mutation=True)
    assert val_ok is True

    # Revoke session
    revoke_session(session_id)
    assert get_session_info(session_id) is None


def test_tenant_safe_backup_and_restore(test_workspaces):
    ws_a_id, ws_b_id = test_workspaces
    tx_uid = uuid.uuid4().hex
    
    # Insert transaction in Workspace A
    with LEDGER_LOCK, get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transactions (
                workspace_id, transaction_type, amount, person_name,
                category, balance_before, balance_after, uid,
                transaction_date, transaction_time, occurred_at, created_at, updated_at
            ) VALUES (?, 'SENT', 250.0, 'Tiffin Center', 'Food', 1000.0, 750.0, ?, '2026-10-07', '12:00:00', '2026-10-07T12:00:00', '2026-10-07T12:00:00', '2026-10-07T12:00:00')
        """, (ws_a_id, tx_uid))
        conn.commit()
    
    # Export Workspace A
    exp_res = export_workspace_to_json(ws_a_id)
    assert exp_res['success'] is True
    payload = exp_res['payload']
    assert payload['format_version'] == "workspace_v1"
    assert payload['workspace_id'] == ws_a_id
    assert "checksum" in payload
    assert len(payload['transactions']) == 1

    # Cross-tenant restore rejection: try to restore Workspace A payload into Workspace B
    cross_res = restore_workspace_from_json(workspace_id=ws_b_id, data_dict=payload)
    assert cross_res['success'] is False
    assert "mismatch" in cross_res['error'].lower()

    # Valid restore into Workspace A
    restore_res = restore_workspace_from_json(workspace_id=ws_a_id, data_dict=payload)
    assert restore_res['success'] is True
    assert restore_res['restored_transactions'] == 1


def test_scheduler_job_deduplication(test_workspaces):
    ws_a_id, _ = test_workspaces
    job_date = "2026-10-07"
    
    # 1. First claim succeeds
    claimed1 = claim_workspace_job(ws_a_id, "daily_digest", job_date)
    assert claimed1 is True

    # 2. Mark completed
    complete_workspace_job(ws_a_id, "daily_digest", job_date, "completed")

    # 3. Second claim fails because already completed
    claimed2 = claim_workspace_job(ws_a_id, "daily_digest", job_date)
    assert claimed2 is False


def test_scoped_undo_log(test_workspaces):
    ws_a_id, ws_b_id = test_workspaces
    
    tx_uid = uuid.uuid4().hex
    deleted_tx = {
        "uid": tx_uid,
        "workspace_id": ws_a_id,
        "amount": 100.0
    }
    
    # Record delete in Workspace A
    rec = record_delete_action(deleted_tx, user_id=5001, workspace_id=ws_a_id)
    assert rec is True


    # Check last action for Workspace B should be None
    last_b = get_last_action(user_id=5001, workspace_id=ws_b_id)
    assert last_b is None

    # Check last action for Workspace A
    last_a = get_last_action(user_id=5001, workspace_id=ws_a_id)
    assert last_a is not None
    assert last_a['uid'] == tx_uid


def test_core_queries_required_workspace_id_default_deny(test_workspaces):
    """P0-10: Verifies workspace_id is strictly required (no None default) and enforces default-deny."""
    from database.models import Transaction
    from database.queries import (
        get_transaction_by_id,
        get_transaction_by_uid,
        update_transaction,
        delete_transaction,
        insert_transaction,
    )
    ws_a_id, ws_b_id = test_workspaces

    t = Transaction(
        amount=150.0,
        transaction_type="SENT",
        person_name="Tenant Store",
        workspace_id=ws_a_id,
    )
    tx_id = insert_transaction(t)
    tx_row = get_transaction_by_id(tx_id, workspace_id=ws_a_id)
    assert tx_row is not None
    uid = tx_row['uid']

    # 1. Calling without workspace_id raises TypeError
    with pytest.raises(TypeError):
        get_transaction_by_id(tx_id)  # type: ignore

    with pytest.raises(TypeError):
        get_transaction_by_uid(uid)  # type: ignore

    with pytest.raises(TypeError):
        update_transaction(tx_id, {"amount": 200.0})  # type: ignore

    with pytest.raises(TypeError):
        delete_transaction(tx_id)  # type: ignore

    # 2. Passing None or empty string results in default-deny (None or False)
    assert get_transaction_by_id(tx_id, workspace_id=None) is None  # type: ignore
    assert get_transaction_by_id(tx_id, workspace_id="") is None
    assert get_transaction_by_uid(uid, workspace_id=None) is None  # type: ignore
    assert get_transaction_by_uid(uid, workspace_id="") is None
    assert update_transaction(tx_id, {"amount": 200.0}, workspace_id=None) is False  # type: ignore
    assert update_transaction(tx_id, {"amount": 200.0}, workspace_id="") is False
    assert delete_transaction(tx_id, workspace_id=None) is False  # type: ignore
    assert delete_transaction(tx_id, workspace_id="") is False

    # 3. Cross-tenant access is denied
    assert get_transaction_by_id(tx_id, workspace_id=ws_b_id) is None
    assert get_transaction_by_uid(uid, workspace_id=ws_b_id) is None
    assert update_transaction(tx_id, {"amount": 200.0}, workspace_id=ws_b_id) is False
    assert delete_transaction(tx_id, workspace_id=ws_b_id) is False

    # 4. Correct workspace succeeds
    assert update_transaction(tx_id, {"amount": 200.0}, workspace_id=ws_a_id) is True
    assert delete_transaction(tx_id, workspace_id=ws_a_id) is True
