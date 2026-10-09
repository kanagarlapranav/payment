import uuid
import pytest
from database.db import get_db_connection, setup_database
from database.models import Transaction, Workspace, WorkspaceMember
from database.queries import (
    get_default_workspace_id,
    get_or_create_workspace,
    get_workspace_by_id,
    get_workspace_by_chat_id,
    get_all_active_workspaces,
    get_workspace_member,
    get_all_workspace_members,
    add_workspace_member,
    update_workspace_member_role,
    get_workspace_setting,
    set_workspace_setting,
    get_balance_setting,
    update_balance_setting,
    get_budget_setting,
    set_budget_setting,
    insert_transaction,
    get_recent_transactions,
    get_transaction_by_reference,
    get_monthly_summary,
    delete_transaction,
    restore_soft_deleted_transaction,
    search_transactions,
)
from services.balance_service import recalculate_all_balances


def test_schema_v4_tables_and_columns_exist():
    """Verify that Schema v4 tables, columns, and indexes exist in the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Core tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row['name'] for row in cursor.fetchall()}
        assert "workspaces" in tables
        assert "workspace_members" in tables
        assert "workspace_settings" in tables

        # 2. Domain tables have workspace_id column
        domain_tables = [
            'transactions', 'custom_menu_items', 'payee_categories',
            'recurring_payments', 'monthly_reviews', 'pending_receipts', 'undo_log'
        ]
        for tbl in domain_tables:
            cursor.execute(f"PRAGMA table_info({tbl})")
            cols = [r['name'] for r in cursor.fetchall()]
            assert "workspace_id" in cols, f"workspace_id column missing from table '{tbl}'"

        # 3. schema_version is bumped to 4
        cursor.execute("SELECT value FROM settings WHERE key = 'schema_version'")
        s_row = cursor.fetchone()
        assert s_row is not None
        assert int(s_row['value']) >= 4

        # 4. default_workspace_id exists in settings
        cursor.execute("SELECT value FROM settings WHERE key = 'default_workspace_id'")
        ws_row = cursor.fetchone()
        assert ws_row is not None
        assert len(str(ws_row['value'])) > 0


def test_default_workspace_and_owner_provisioned():
    """Verify default workspace was provisioned and owner assigned."""
    def_ws_id = get_default_workspace_id()
    assert def_ws_id is not None

    ws = get_workspace_by_id(def_ws_id)
    assert ws is not None
    assert ws['is_active'] == 1

    members = get_all_workspace_members(def_ws_id)
    # At least the primary owner should exist if TELEGRAM_USER_ID was configured
    if members:
        assert any(m['role'] == 'owner' for m in members)


def test_workspace_crud_operations():
    """Verify creating, looking up, and managing workspaces and their members."""
    test_chat_id = -100999888777
    ws = get_or_create_workspace(chat_id=test_chat_id, chat_type="supergroup", title="Finance Team Group")
    assert ws is not None
    assert ws['chat_id'] == test_chat_id
    assert ws['chat_type'] == "supergroup"
    assert ws['title'] == "Finance Team Group"

    # Lookup by ID and by chat_id
    ws_by_id = get_workspace_by_id(ws['id'])
    assert ws_by_id is not None
    assert ws_by_id['id'] == ws['id']

    ws_by_chat = get_workspace_by_chat_id(test_chat_id)
    assert ws_by_chat is not None
    assert ws_by_chat['id'] == ws['id']

    # Add members with different roles
    m_admin = add_workspace_member(ws['id'], telegram_user_id=11111, username="alice", display_name="Alice", role="admin")
    assert m_admin['role'] == "admin"

    m_viewer = add_workspace_member(ws['id'], telegram_user_id=22222, username="bob", display_name="Bob", role="viewer")
    assert m_viewer['role'] == "viewer"

    # Check member query
    fetched_admin = get_workspace_member(ws['id'], 11111)
    assert fetched_admin is not None
    assert fetched_admin['role'] == "admin"
    assert fetched_admin['username'] == "alice"

    # Update role
    updated = update_workspace_member_role(ws['id'], 22222, "member")
    assert updated is True
    fetched_bob = get_workspace_member(ws['id'], 22222)
    assert fetched_bob['role'] == "member"

    # Invalid role raises ValueError
    with pytest.raises(ValueError):
        update_workspace_member_role(ws['id'], 22222, "superuser")


def test_workspace_settings_dual_read_and_isolation():
    """Verify per-workspace settings isolation and fallback to global settings."""
    def_ws_id = get_default_workspace_id()
    custom_ws = get_or_create_workspace(chat_id=888123, chat_type="private", title="Custom User")
    custom_ws_id = custom_ws['id']

    # Set budget for custom workspace
    set_workspace_setting(custom_ws_id, "monthly_budget", "15000.0")
    assert get_workspace_setting(custom_ws_id, "monthly_budget") == "15000.0"

    # Default workspace should retain its own budget or fallback
    set_workspace_setting(def_ws_id, "monthly_budget", "30000.0")
    assert get_workspace_setting(def_ws_id, "monthly_budget") == "30000.0"

    # Helper getters/setters test
    set_budget_setting(20000.0, workspace_id=custom_ws_id)
    assert get_budget_setting(workspace_id=custom_ws_id) == 20000.0
    assert get_budget_setting(workspace_id=def_ws_id) == 30000.0

    # Fallback test: A key that does not exist in workspace_settings falls back to settings
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fallback_test_key', 'global_val')")
    assert get_workspace_setting(custom_ws_id, "fallback_test_key") == "global_val"


def test_transaction_insert_and_balance_isolation():
    """Verify transactions and balances are strictly isolated between two separate workspaces."""
    ws1 = get_or_create_workspace(chat_id=7001, chat_type="private", title="Workspace 1")
    ws2 = get_or_create_workspace(chat_id=7002, chat_type="private", title="Workspace 2")
    ws1_id = ws1['id']
    ws2_id = ws2['id']

    # Set initial balances
    set_workspace_setting(ws1_id, "initial_balance", "5000.0")
    set_workspace_setting(ws2_id, "initial_balance", "10000.0")
    update_balance_setting(5000.0, workspace_id=ws1_id)
    update_balance_setting(10000.0, workspace_id=ws2_id)

    # Insert transaction in ws1: SENT 1500 -> balance should become 3500
    t1 = Transaction(
        transaction_type="SENT",
        amount=1500.0,
        person_name="Vendor A",
        transaction_date="2026-10-06",
        transaction_time="10:00:00",
        reference_number="REF_WS1_001",
        workspace_id=ws1_id
    )
    insert_transaction(t1)

    # Insert transaction in ws2: RECEIVED 3000 -> balance should become 13000
    t2 = Transaction(
        transaction_type="RECEIVED",
        amount=3000.0,
        person_name="Client B",
        transaction_date="2026-10-06",
        transaction_time="10:30:00",
        reference_number="REF_WS2_001",
        workspace_id=ws2_id
    )
    insert_transaction(t2)

    # Verify balances are completely independent
    assert get_balance_setting(workspace_id=ws1_id) == 3500.0
    assert get_balance_setting(workspace_id=ws2_id) == 13000.0

    # Verify recent transactions queries are isolated
    txs_ws1 = get_recent_transactions(limit=10, workspace_id=ws1_id)
    assert len(txs_ws1) == 1
    assert txs_ws1[0]['reference_number'] == "REF_WS1_001"
    assert txs_ws1[0]['balance_after'] == 3500.0

    txs_ws2 = get_recent_transactions(limit=10, workspace_id=ws2_id)
    assert len(txs_ws2) == 1
    assert txs_ws2[0]['reference_number'] == "REF_WS2_001"
    assert txs_ws2[0]['balance_after'] == 13000.0


def test_reference_number_uniqueness_scoped_per_workspace():
    """Verify that duplicate reference numbers are blocked inside a workspace but allowed across different workspaces."""
    ws_alpha = get_or_create_workspace(chat_id=8001, chat_type="private", title="Alpha")
    ws_beta = get_or_create_workspace(chat_id=8002, chat_type="private", title="Beta")
    shared_ref = f"UPI_REF_{uuid.uuid4().hex[:8]}"

    t_alpha = Transaction(
        transaction_type="SENT",
        amount=100.0,
        person_name="Merchant",
        transaction_date="2026-10-06",
        reference_number=shared_ref,
        workspace_id=ws_alpha['id']
    )
    insert_transaction(t_alpha)

    # Duplicate inside same workspace MUST fail
    t_alpha_dup = Transaction(
        transaction_type="SENT",
        amount=200.0,
        person_name="Merchant",
        transaction_date="2026-10-06",
        reference_number=shared_ref,
        workspace_id=ws_alpha['id']
    )
    with pytest.raises(ValueError, match="Duplicate live reference number"):
        insert_transaction(t_alpha_dup)

    # Same reference inside DIFFERENT workspace MUST succeed
    t_beta = Transaction(
        transaction_type="SENT",
        amount=100.0,
        person_name="Merchant",
        transaction_date="2026-10-06",
        reference_number=shared_ref,
        workspace_id=ws_beta['id']
    )
    tx_id_beta = insert_transaction(t_beta)
    assert tx_id_beta > 0


def test_dual_read_backward_compatibility():
    """Verify that callers omitting workspace_id default transparently to the default workspace."""
    def_ws_id = get_default_workspace_id()

    # Insert transaction without explicit workspace_id
    t_legacy = Transaction(
        transaction_type="SENT",
        amount=250.0,
        person_name="Coffee Shop",
        transaction_date="2026-10-06",
        reference_number=f"LEGACY_{uuid.uuid4().hex[:6]}"
    )
    tx_id = insert_transaction(t_legacy)
    assert tx_id > 0

    # Ensure it was assigned the default workspace
    assert t_legacy.workspace_id == def_ws_id

    # Caller without workspace_id gets the record
    recent = get_recent_transactions(limit=20)
    found = any(r['id'] == tx_id for r in recent)
    assert found is True

    # Search without workspace_id
    res = search_transactions(person="Coffee Shop")
    assert any(r['id'] == tx_id for r in res)


def test_soft_delete_and_restore_in_workspace():
    """Verify soft-delete and restore re-balance only the workspace containing the transaction."""
    ws = get_or_create_workspace(chat_id=9001, chat_type="private", title="Delete Test")
    ws_id = ws['id']
    set_workspace_setting(ws_id, "initial_balance", "1000.0")
    update_balance_setting(1000.0, workspace_id=ws_id)

    t = Transaction(
        transaction_type="SENT",
        amount=300.0,
        person_name="Store",
        transaction_date="2026-10-06",
        reference_number=f"DEL_{uuid.uuid4().hex[:6]}",
        workspace_id=ws_id
    )
    tx_id = insert_transaction(t)
    assert get_balance_setting(workspace_id=ws_id) == 700.0

    # Soft delete
    deleted = delete_transaction(tx_id, workspace_id=ws_id)
    assert deleted is True
    # Balance should restore back to 1000.0
    assert get_balance_setting(workspace_id=ws_id) == 1000.0

    # Restore
    restored = restore_soft_deleted_transaction(tx_id=tx_id)
    assert restored is True
    # Balance should re-subtract 300.0 -> 700.0
    assert get_balance_setting(workspace_id=ws_id) == 700.0
