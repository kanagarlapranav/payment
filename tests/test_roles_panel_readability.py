import pytest
import os
import shutil
import tempfile
from unittest.mock import patch

from database.db import setup_database, get_db_connection
from database.queries import get_or_create_workspace, add_workspace_member
from bot.commands import render_permissions_list_payload, render_user_permission_card


@pytest.fixture(autouse=True)
def fresh_db():
    setup_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members")
        conn.execute("DELETE FROM access_requests")
        conn.execute("DELETE FROM workspaces")
        conn.commit()
    yield
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members")
        conn.execute("DELETE FROM access_requests")
        conn.execute("DELETE FROM workspaces")
        conn.commit()


def test_grouped_by_person_3_users_3_blocks():
    """3 users across 4 workspaces render as 3 person-blocks; each block lists only that user's workspaces; 'Total Users: 3' matches the block count."""
    ws1 = get_or_create_workspace(title="Payment (Group)", creator_user_id=1001, chat_id=-1001, chat_type="group")
    ws2 = get_or_create_workspace(title="Test Workspace", creator_user_id=1001, chat_id=-1002, chat_type="group")
    ws3 = get_or_create_workspace(title="Nagendra (Personal)", creator_user_id=1002, chat_id=1002, chat_type="dm")
    ws4 = get_or_create_workspace(title="Side Ledger", creator_user_id=1001, chat_id=-1004, chat_type="group")

    # User 1: 1001 (Owner) in ws1, ws2, ws4
    add_workspace_member(ws1.id, 1001, display_name="Owner", username="owner", role="owner")
    add_workspace_member(ws2.id, 1001, display_name="Owner", username="owner", role="owner")
    add_workspace_member(ws4.id, 1001, display_name="Owner", username="owner", role="owner")

    # User 2: 1002 (Nagendra) in ws2, ws3
    add_workspace_member(ws2.id, 1002, display_name="Nagendra", username="nagendra", role="admin")
    add_workspace_member(ws3.id, 1002, display_name="Nagendra", username="nagendra", role="member")

    # User 3: 1003 (Akhil) in ws1
    add_workspace_member(ws1.id, 1003, display_name="Akhil akhil", username="akhil", role="viewer")

    text, markup = render_permissions_list_payload(workspace_id=ws1.id)

    # 1. Total Users matches block count: 3
    assert "<b>Total Users:</b> 3" in text

    # 2. Each block lists only that user's workspaces indented underneath
    # User 1 block should contain ws1, ws2, ws4
    assert "Payment (Group) — Owner" in text
    assert "Side Ledger — Owner" in text

    # User 2 block should contain ws2, ws3
    assert "Test Workspace — Admin" in text
    assert "Nagendra (Personal) — Member" in text

    # User 3 block should contain ws1
    assert "Payment (Group) — Viewer" in text

    # Verify there are 3 user buttons
    buttons = [btn for row in markup.inline_keyboard for btn in row if btn.callback_data.startswith("perm_view:")]
    assert len(buttons) == 3


def test_name_resolution_across_workspaces_no_raw_id():
    """A user with an empty display_name/username in one workspace but a name in another shows the name on EVERY row and button; raw 'ID 8379948573' never appears."""
    ws1 = get_or_create_workspace(title="Workspace A", creator_user_id=8379948573, chat_id=-2001, chat_type="group")
    ws2 = get_or_create_workspace(title="Workspace B", creator_user_id=8379948573, chat_id=-2002, chat_type="group")

    # In ws1: empty display name and empty username
    add_workspace_member(ws1.id, 8379948573, display_name="", username="", role="owner")
    # In ws2: has display name "Pranav K" and username "pranav"
    add_workspace_member(ws2.id, 8379948573, display_name="Pranav K", username="pranav", role="owner")

    text, markup = render_permissions_list_payload(workspace_id=ws1.id)

    # The raw "ID 8379948573" must never appear as display name
    assert "ID 8379948573" not in text
    assert "Pranav K" in text

    for row in markup.inline_keyboard:
        for btn in row:
            assert "ID 8379948573" not in btn.text
            if btn.callback_data == "perm_view:8379948573":
                assert "Pranav K" in btn.text or "@pranav" in btn.text


def test_legend_line_present():
    """The legend line is present near the top of the panel with all four roles."""
    ws = get_or_create_workspace(title="Main Workspace", creator_user_id=3001, chat_id=-3001, chat_type="group")
    add_workspace_member(ws.id, 3001, display_name="Boss", role="owner")

    text, _ = render_permissions_list_payload(workspace_id=ws.id)

    expected_legend = "👑 Owner — everything • 🛡️ Admin — add/edit, manage users • 👤 Member — add and see • 👁️ Viewer — see only"
    assert expected_legend in text


def test_button_labels_show_current_workspace_role_only():
    """Button labels: role Admin in CURRENT workspace shows '(ADM)'; user with NO role in current workspace shows no abbreviation."""
    ws_current = get_or_create_workspace(title="Payment (Group)", creator_user_id=4001, chat_id=-4001, chat_type="group")
    ws_other = get_or_create_workspace(title="Test Workspace", creator_user_id=4001, chat_id=-4002, chat_type="group")

    # User 1: 4001 Owner in current
    add_workspace_member(ws_current.id, 4001, display_name="Owner", role="owner")

    # User 2: 4002 (Alice) is Admin in CURRENT workspace
    add_workspace_member(ws_current.id, 4002, display_name="Alice", role="admin")

    # User 3: 4003 (Nagendra) is Admin in OTHER workspace only, NO role in current
    add_workspace_member(ws_other.id, 4003, display_name="Nagendra", username="nagendra", role="admin")

    _, markup = render_permissions_list_payload(workspace_id=ws_current.id)

    btn_alice = next(btn for row in markup.inline_keyboard for btn in row if btn.callback_data == "perm_view:4002")
    btn_nagendra = next(btn for row in markup.inline_keyboard for btn in row if btn.callback_data == "perm_view:4003")

    # Alice is Admin in current workspace -> shows (ADM)
    assert "(ADM)" in btn_alice.text

    # Nagendra has NO role in current workspace -> NO abbreviation
    assert "(ADM)" not in btn_nagendra.text
    assert "(MEM)" not in btn_nagendra.text
    assert "(VIE)" not in btn_nagendra.text
    assert "(OWN)" not in btn_nagendra.text
    assert "Nagendra" in btn_nagendra.text or "@nagendra" in btn_nagendra.text


def test_ordering_current_workspace_users_first_with_separator():
    """Users with a role in the current workspace are listed before users who only belong to other workspaces, with separator line."""
    ws_current = get_or_create_workspace(title="Current WS", creator_user_id=5001, chat_id=-5001, chat_type="group")
    ws_other = get_or_create_workspace(title="Other WS", creator_user_id=5001, chat_id=-5002, chat_type="group")

    # User 1 in current WS
    add_workspace_member(ws_current.id, 5001, display_name="User Current", role="owner")
    # User 2 in other WS only
    add_workspace_member(ws_other.id, 5002, display_name="User Other", role="member")

    text, markup = render_permissions_list_payload(workspace_id=ws_current.id)

    # Separator must be present
    assert "— Other workspaces —" in text

    # User Current must appear before separator, User Other after
    pos_current = text.find("User Current")
    pos_sep = text.find("— Other workspaces —")
    pos_other = text.find("User Other")

    assert pos_current != -1
    assert pos_sep != -1
    assert pos_other != -1
    assert pos_current < pos_sep < pos_other

    # Buttons must also follow current workspace first, then other
    buttons = [btn for row in markup.inline_keyboard for btn in row if btn.callback_data.startswith("perm_view:")]
    assert buttons[0].callback_data == "perm_view:5001"
    assert buttons[1].callback_data == "perm_view:5002"


def test_detail_card_shows_resolved_name_not_unknown():
    """The detail card shows the resolved name (not 'Unknown') when the name exists on another row for the same user."""
    ws1 = get_or_create_workspace(title="Workspace 1", creator_user_id=6001, chat_id=-6001, chat_type="group")
    ws2 = get_or_create_workspace(title="Workspace 2", creator_user_id=6001, chat_id=-6002, chat_type="group")

    # User 6002 has empty display name on first row (ws1), but 'Akhil Sharma' on ws2
    add_workspace_member(ws1.id, 6002, display_name="", username="", role="viewer")
    add_workspace_member(ws2.id, 6002, display_name="Akhil Sharma", username="akhil_s", role="viewer")

    card_text, _ = render_user_permission_card(target_uid=6002, workspace_id=ws1.id)

    assert "Name:</b> Unknown" not in card_text
    assert "Akhil Sharma" in card_text

