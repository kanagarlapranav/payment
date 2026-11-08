import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from telegram import Update, User, Chat, Message

from database.db import setup_database, get_db_connection
from database.models import Transaction
from database.queries import (
    get_or_create_workspace,
    add_workspace_member,
    insert_transaction
)
from bot.auth import RequestContext, get_effective_view_user_id
from bot.commands import render_workspaces_view


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "group", chat_title: str = "Payment Group"):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"Name_{user_id}"
    user.full_name = f"Name_{user_id}"
    update.effective_user = user
    
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    chat.title = chat_title
    update.effective_chat = chat

    msg = MagicMock(spec=Message)
    msg.text = ""
    msg.chat_id = chat_id
    msg.message_id = 101
    msg.reply_text = AsyncMock()
    msg.from_user = user
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


@pytest.fixture(autouse=True)
def setup_db():
    setup_database()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM workspace_members WHERE telegram_user_id IN (8801, 8802, 8803)")
        conn.commit()


def test_viewer_full_ledger_in_group_but_own_rows_in_dm_workspace():
    """Error 2: Viewer sees full ledger in group workspaces, but own rows only in personal DM workspaces."""
    group_ws = MagicMock()
    group_ws.chat_type = "group"
    group_ws.id = "ws_grp_1"
    group_ws.title = "Group Workspace"

    dm_ws = MagicMock()
    dm_ws.chat_type = "dm"
    dm_ws.id = "ws_dm_1"
    dm_ws.title = "Personal Workspace"

    caller_id = 8802

    # In group workspace, viewer sees full ledger (None)
    ctx_grp = RequestContext(
        workspace_id="ws_grp_1",
        chat_id=-1008801,
        chat_type="group",
        user_id=caller_id,
        role="viewer",
        workspace=group_ws
    )
    assert get_effective_view_user_id(ctx_grp, caller_id) is None

    # In personal DM workspace, viewer sees own rows only (caller_id)
    ctx_dm = RequestContext(
        workspace_id="ws_dm_1",
        chat_id=caller_id,
        chat_type="private",
        user_id=caller_id,
        role="viewer",
        workspace=dm_ws
    )
    assert get_effective_view_user_id(ctx_dm, caller_id) == caller_id


def test_member_visibility_keyed_on_workspace_chat_type_not_arriving_chat():
    """Error 3: Member visibility is keyed on the workspace's own chat_type, not the arriving chat."""
    group_ws = MagicMock()
    group_ws.chat_type = "group"
    group_ws.id = "ws_grp_2"
    group_ws.title = "Group Workspace"

    caller_id = 8802

    # Member in group chat viewing group workspace -> None (full ledger)
    ctx_grp_chat = RequestContext(
        workspace_id="ws_grp_2",
        chat_id=-1008802,
        chat_type="group",
        user_id=caller_id,
        role="member",
        workspace=group_ws
    )
    assert get_effective_view_user_id(ctx_grp_chat, caller_id) is None

    # Member in DM switched to the SAME group workspace (arriving chat_type is private, but ws.chat_type is group)
    ctx_dm_switched = RequestContext(
        workspace_id="ws_grp_2",
        chat_id=caller_id,
        chat_type="private",
        user_id=caller_id,
        role="member",
        workspace=group_ws
    )
    # MUST see full ledger because workspace is group workspace!
    assert get_effective_view_user_id(ctx_dm_switched, caller_id) is None


def test_removal_of_fix_title_hack_and_dead_setting():
    """Error 4: A workspace titled 'Fix Something' does NOT trigger strict per-member isolation."""
    group_ws = MagicMock()
    group_ws.chat_type = "group"
    group_ws.id = "ws_grp_fix"
    group_ws.title = "Fix 8b Workspace"

    caller_id = 8802
    ctx_fix = RequestContext(
        workspace_id="ws_grp_fix",
        chat_id=-1008803,
        chat_type="group",
        user_id=caller_id,
        role="member",
        workspace=group_ws
    )
    # Must NOT return caller_id from the title hack; must return None
    assert get_effective_view_user_id(ctx_fix, caller_id) is None


def test_dm_workspaces_view_reverted_for_non_owners():
    """Error 5: In DM, non-owner member/viewer sees only their own DM workspace, with no group switch buttons."""
    owner_id = 8801
    member_id = 8802
    group_chat_id = -1008804

    ws_grp = get_or_create_workspace(
        title="Active Payment Group",
        creator_user_id=owner_id,
        chat_id=group_chat_id,
        chat_type="group"
    )
    add_workspace_member(ws_grp.id, owner_id, role="owner")
    add_workspace_member(ws_grp.id, member_id, role="member")

    ws_dm = get_or_create_workspace(
        title=f"User {member_id}",
        creator_user_id=member_id,
        chat_id=member_id,
        chat_type="dm"
    )
    add_workspace_member(ws_dm.id, member_id, role="owner")

    # Member opens /workspaces in DM
    u_dm = make_mock_update(user_id=member_id, chat_id=member_id, chat_type="private")
    text, markup = render_workspaces_view(u_dm)

    # Group workspace must NOT be listed as an available ledger for non-owner in DM
    assert "Active Payment Group" not in text

    # No switch button for group workspace
    for row in markup.inline_keyboard:
        for btn in row:
            assert btn.callback_data != f"ws_switch:{ws_grp.id}", "Group switch button must NOT be present for non-owner in DM"

