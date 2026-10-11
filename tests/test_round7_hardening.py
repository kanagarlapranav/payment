import os
import pytest
from unittest.mock import MagicMock
from decimal import Decimal
from bot.feature_registry import (
    FEATURES,
    set_feature_permission,
    reset_feature_permissions,
    lookup_feature_permission,
    feature_allowed,
)
from database.queries import (
    create_custom_workspace,
    get_default_workspace_id,
    add_workspace_member,
)


def test_invalid_feature_key_rejected():
    """Verify set_feature_permission rejects non-canonical feature keys."""
    ws_id = get_default_workspace_id()
    assert set_feature_permission(ws_id, "nonexistent_secret_key", "admin", True) is False
    assert lookup_feature_permission(ws_id, "nonexistent_secret_key", "admin") is None
    # Reset should also safely ignore invalid feature keys
    reset_feature_permissions(ws_id, "nonexistent_secret_key")
    assert lookup_feature_permission(ws_id, "nonexistent_secret_key", "admin") is None


def test_canonical_feature_keys_accepted():
    """Verify set_feature_permission accepts all 15 canonical feature keys."""
    ws_id = get_default_workspace_id()
    for feat_key, _, _ in FEATURES:
        assert set_feature_permission(ws_id, feat_key, "viewer", True) is True
        assert lookup_feature_permission(ws_id, feat_key, "viewer") is True
        # Reset specific key
        reset_feature_permissions(ws_id, feat_key)


def test_group_workspaces_personal_ledger_isolation(monkeypatch):
    """Verify group chat /workspaces in admin mode never leaks other users' personal ledgers."""
    from bot.commands import render_workspaces_view
    from database.queries import get_or_create_workspace, add_workspace_member
    import config

    user_a_id = 999901
    user_b_id = 999902
    group_chat_id = -100999999

    # Genuinely enter admin mode by setting config.TELEGRAM_USER_ID to the test user
    monkeypatch.setattr(config, "TELEGRAM_USER_ID", str(user_a_id))

    # Create group workspace and personal workspace for User B
    grp_ws = get_or_create_workspace(
        title="Engineering Group",
        creator_user_id=user_a_id,
        chat_id=group_chat_id,
        chat_type="group"
    )
    user_b_dm = get_or_create_workspace(
        title="User B Private Ledger",
        creator_user_id=user_b_id,
        chat_id=user_b_id,
        chat_type="dm"
    )
    user_b_dm_id = user_b_dm.id
    add_workspace_member(grp_ws.id, user_a_id, role="admin")

    # Mock Telegram Update for User A in the group chat
    mock_update = MagicMock()
    mock_update.effective_chat.id = group_chat_id
    mock_update.effective_chat.type = "group"
    mock_update.effective_chat.title = "Engineering Group"
    mock_update.effective_user.id = user_a_id

    # Render view in group chat
    text, keyboard = render_workspaces_view(mock_update)

    # Invariant: admin mode in group chats must genuinely render admin controls
    assert "Engineering Group" in text
    assert any("➕ New Ledger" in btn.text or btn.callback_data == "ws_new_prompt"
               for row in keyboard.inline_keyboard for btn in row)

    # Invariant: in a group chat, the admin-mode /workspaces view must not surface other users' personal DM ledgers
    # (no foreign "Personal Ledgers:" section, no foreign DM workspace titles or switch buttons)
    assert "Personal Ledgers:" not in text
    assert "User B Private Ledger" not in text

    # And must not appear in any button callback or label
    for row in keyboard.inline_keyboard:
        for btn in row:
            assert "User B Private" not in btn.text
            if btn.callback_data and btn.callback_data.startswith("ws_switch:"):
                assert btn.callback_data != f"ws_switch:{user_b_dm_id}"


def test_all_canonical_feature_keys_have_production_callsites():
    """Verify all 15 canonical keys are actively referenced in production code."""
    import re
    prod_files = ['bot/commands.py', 'bot/handlers.py', 'bot/callbacks/nav.py']
    combined = ''
    for p in prod_files:
        with open(p, 'r', encoding='utf-8') as f:
            combined += f.read() + '\n'

    calls = set(re.findall(r'feature_allowed\([^)]*["\']([^"\']+)["\']\)', combined))
    for key, _, _ in FEATURES:
        assert key in calls, f"Canonical feature key '{key}' has no production call site!"
