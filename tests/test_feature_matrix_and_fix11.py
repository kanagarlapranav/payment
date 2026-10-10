import pytest
from unittest.mock import MagicMock, AsyncMock
from bot.feature_registry import (
    FEATURES, ROLES, seed_default_feature_permissions,
    lookup_feature_permission, set_feature_permission,
    reset_feature_permissions, get_workspace_feature_matrix,
    feature_allowed
)
from database.db import setup_database, get_db_connection
from database.queries import get_or_create_workspace, add_workspace_member
import config

TEST_OWNER_ID = 888999111
TEST_ADMIN_ID = 222333444
TEST_MEMBER_ID = 333444555
TEST_VIEWER_ID = 444555666

@pytest.fixture(autouse=True)
def clean_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_fix11.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("database.db.DB_PATH", db_file)
    monkeypatch.setattr("config.TELEGRAM_USER_ID", TEST_OWNER_ID)
    setup_database()
    yield

def test_feature_matrix_defaults_and_seeding():
    ws = get_or_create_workspace(chat_id=12345, chat_type="group", title="Test Group")
    ws_id = ws["id"]
    seed_default_feature_permissions(ws_id)
    matrix = get_workspace_feature_matrix(ws_id)
    assert len(matrix) == len(FEATURES)
    # Default: viewer has history, but not report or export
    assert matrix["history"]["viewer"] is True
    assert matrix["report"]["viewer"] is False
    assert matrix["export"]["member"] is True

def test_feature_matrix_toggling_and_isolation():
    ws_a = get_or_create_workspace(chat_id=101, chat_type="group", title="Group A")
    ws_b = get_or_create_workspace(chat_id=102, chat_type="group", title="Group B")
    ws_a_id = ws_a["id"]
    ws_b_id = ws_b["id"]
    seed_default_feature_permissions(ws_a_id)
    seed_default_feature_permissions(ws_b_id)

    # Add member to both
    add_workspace_member(ws_a_id, TEST_MEMBER_ID, "member")
    add_workspace_member(ws_b_id, TEST_MEMBER_ID, "member")

    # Member can initially export
    assert feature_allowed(ws_a_id, TEST_MEMBER_ID, "export") is True
    assert feature_allowed(ws_b_id, TEST_MEMBER_ID, "export") is True

    # Revoke export for member in ws_a only
    set_feature_permission(ws_a_id, "export", "member", False)
    assert feature_allowed(ws_a_id, TEST_MEMBER_ID, "export") is False
    # ws_b remains unaffected
    assert feature_allowed(ws_b_id, TEST_MEMBER_ID, "export") is True

    # Reset ws_a export feature
    reset_feature_permissions(ws_a_id, "export")
    assert feature_allowed(ws_a_id, TEST_MEMBER_ID, "export") is True

def test_global_owner_bypasses_all_matrix_restrictions():
    ws = get_or_create_workspace(chat_id=999, chat_type="group", title="Restricted Group")
    ws_id = ws["id"]
    # Revoke everything for all roles
    for feat_key, _, _ in FEATURES:
        for r in ROLES:
            set_feature_permission(ws_id, feat_key, r, False)

    # Non-owner is denied
    add_workspace_member(ws_id, TEST_MEMBER_ID, "member")
    assert feature_allowed(ws_id, TEST_MEMBER_ID, "history") is False

    # Global owner is always allowed
    assert feature_allowed(ws_id, TEST_OWNER_ID, "history") is True
    assert feature_allowed(ws_id, TEST_OWNER_ID, "export") is True

def test_owner_self_lockout_prevention():
    import asyncio
    from bot.commands import settings_command

    ws = get_or_create_workspace(chat_id=TEST_OWNER_ID, chat_type="private", title="Owner Ledger")
    update = MagicMock()
    update.effective_user.id = TEST_OWNER_ID
    update.effective_chat.id = TEST_OWNER_ID
    update.effective_chat.type = "private"
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    ctx = MagicMock()
    ctx.args = ["restrict", str(TEST_OWNER_ID)]

    asyncio.run(settings_command(update, ctx))
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "cannot restrict yourself" in reply.lower()
