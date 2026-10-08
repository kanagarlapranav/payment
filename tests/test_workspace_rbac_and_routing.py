import uuid
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message

from database.db import get_db_connection, setup_database
from database.models import Transaction, Workspace, WorkspaceMember
from database.queries import (
    get_or_create_workspace,
    get_workspace_by_id,
    get_workspace_member,
    get_all_workspace_members,
    add_workspace_member,
    update_workspace_member_role,
    insert_transaction,
    get_transaction_by_id,
    update_transaction,
    delete_transaction,
    get_recent_transactions,
    search_transactions,
    get_transactions_by_date
)
from services.balance_service import recalculate_all_balances, get_today_summary
from bot.auth import (
    RequestContext,
    WORKSPACE_ROLE_HIERARCHY,
    get_workspace_context,
    resolve_workspace_context
)
from bot.commands import workspace_command, members_command, setrole_command
from bot.handlers import (
    set_pending_transaction,
    fetch_pending_transaction,
    pop_pending_transaction
)


@pytest.fixture(autouse=True)
def init_db():
    setup_database()


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "private", text: str = ""):
    update = MagicMock(spec=Update)
    
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = f"user_{user_id}"
    user.first_name = f"Name_{user_id}"
    update.effective_user = user
    
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type
    chat.title = f"Chat_{chat_id}"
    update.effective_chat = chat

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.chat_id = chat_id
    msg.message_id = 999
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_message = msg
    update.callback_query = None

    return update


def test_workspace_auto_provisioning_dm_and_group():
    """Verify that private DMs create 'dm' workspaces and groups create 'group' workspaces with proper owner registration."""
    # 1. Private DM Auto-provisioning
    dm_user = 50001
    dm_chat = 50001
    ws_dm = get_or_create_workspace(
        chat_id=str(dm_chat),
        chat_type="private",
        title="Alice DM",
        creator_user_id=dm_user,
        username="alice",
        display_name="Alice"
    )
    assert ws_dm is not None
    assert ws_dm.chat_type == "dm"
    assert ws_dm.title == "Alice DM"

    member_dm = get_workspace_member(ws_dm.id, dm_user)
    assert member_dm is not None
    assert member_dm.role == "owner"

    # 2. Group Auto-provisioning
    group_owner = 60001
    group_chat = -70001
    ws_grp = get_or_create_workspace(
        chat_id=str(group_chat),
        chat_type="group",
        title="Project Titan",
        creator_user_id=group_owner,
        username="titan_creator",
        display_name="Titan Creator"
    )
    assert ws_grp is not None
    assert ws_grp.chat_type == "group"
    assert ws_grp.title == "Project Titan"

    member_owner = get_workspace_member(ws_grp.id, group_owner)
    assert member_owner is not None
    assert member_owner.role == "owner"

    # Subsequent user joins group as 'member'
    colleague = 60002
    add_workspace_member(
        workspace_id=ws_grp.id,
        telegram_user_id=colleague,
        role="member",
        username="titan_dev",
        display_name="Titan Dev"
    )
    member_colleague = get_workspace_member(ws_grp.id, colleague)
    assert member_colleague is not None
    assert member_colleague.role == "member"


def test_rbac_hierarchy_and_request_context():
    """Verify role hierarchy comparisons and RequestContext.has_role() behavior."""
    assert WORKSPACE_ROLE_HIERARCHY['owner'] > WORKSPACE_ROLE_HIERARCHY['admin']
    assert WORKSPACE_ROLE_HIERARCHY['admin'] > WORKSPACE_ROLE_HIERARCHY['member']
    assert WORKSPACE_ROLE_HIERARCHY['member'] > WORKSPACE_ROLE_HIERARCHY['viewer']

    # Viewer
    ctx_viewer = RequestContext(workspace_id="ws_1", telegram_user_id=1, role="viewer")
    assert ctx_viewer.has_role("viewer") is True
    assert ctx_viewer.has_role("member") is False
    assert ctx_viewer.has_role("admin") is False
    assert ctx_viewer.has_role("owner") is False

    # Member
    ctx_member = RequestContext(workspace_id="ws_1", telegram_user_id=2, role="member")
    assert ctx_member.has_role("viewer") is True
    assert ctx_member.has_role("member") is True
    assert ctx_member.has_role("admin") is False
    assert ctx_member.has_role("owner") is False

    # Admin
    ctx_admin = RequestContext(workspace_id="ws_1", telegram_user_id=3, role="admin")
    assert ctx_admin.has_role("viewer") is True
    assert ctx_admin.has_role("member") is True
    assert ctx_admin.has_role("admin") is True
    assert ctx_admin.has_role("owner") is False

    # Owner
    ctx_owner = RequestContext(workspace_id="ws_1", telegram_user_id=4, role="owner")
    assert ctx_owner.has_role("viewer") is True
    assert ctx_owner.has_role("member") is True
    assert ctx_owner.has_role("admin") is True
    assert ctx_owner.has_role("owner") is True


def test_resolve_workspace_context_policy_enforcement():
    """Verify resolve_workspace_context() permissions checking."""
    async def _test():
        chat_id = -88001
        owner_id = 90001
        viewer_id = 90002

        ws = get_or_create_workspace(
            chat_id=str(chat_id),
            chat_type="group",
            title="Finance Group",
            creator_user_id=owner_id
        )
        add_workspace_member(ws.id, viewer_id, role="viewer")

        # Viewer update
        up_viewer = make_mock_update(user_id=viewer_id, chat_id=chat_id, chat_type="group")
        
        # Viewer can access read_only
        ctx_ro = await resolve_workspace_context(up_viewer, required_policy="read_only")
        assert ctx_ro is not None
        assert ctx_ro.role == "viewer"

        # Viewer CANNOT access admin
        ctx_admin_fail = await resolve_workspace_context(up_viewer, required_policy="admin")
        assert ctx_admin_fail is None
        up_viewer.message.reply_text.assert_called_once()
        assert "Access Restricted" in up_viewer.message.reply_text.call_args[0][0] or "Permission Denied" in up_viewer.message.reply_text.call_args[0][0]

        # Owner update
        up_owner = make_mock_update(user_id=owner_id, chat_id=chat_id, chat_type="group")
        ctx_admin_pass = await resolve_workspace_context(up_owner, required_policy="admin")
        assert ctx_admin_pass is not None
        assert ctx_admin_pass.role == "owner"

    asyncio.run(_test())


def test_cross_workspace_transaction_isolation():
    """Ensure transactions logged in Workspace A are completely isolated from Workspace B."""
    ws_a = get_or_create_workspace(chat_id="101010", chat_type="private", title="Chat A", creator_user_id=101)
    ws_b = get_or_create_workspace(chat_id="202020", chat_type="private", title="Chat B", creator_user_id=202)

    tx_a = Transaction(
        amount=500.0,
        transaction_type="SENT",
        person_name="Vendor A",
        workspace_id=ws_a.id,
        payment_app="Google Pay"
    )
    id_a = insert_transaction(tx_a)
    assert id_a > 0

    tx_b = Transaction(
        amount=1200.0,
        transaction_type="RECEIVED",
        person_name="Client B",
        workspace_id=ws_b.id,
        payment_app="PhonePe"
    )
    id_b = insert_transaction(tx_b)
    assert id_b > 0

    # 1. Recent transactions scoping
    recent_a = get_recent_transactions(limit=10, workspace_id=ws_a.id)
    payees_a = [t['person_name'] for t in recent_a]
    assert "Vendor A" in payees_a
    assert "Client B" not in payees_a

    recent_b = get_recent_transactions(limit=10, workspace_id=ws_b.id)
    payees_b = [t['person_name'] for t in recent_b]
    assert "Client B" in payees_b
    assert "Vendor A" not in payees_b

    # 2. Search transactions scoping
    search_a = search_transactions(query_text="", workspace_id=ws_a.id)
    assert any(t['id'] == id_a for t in search_a)
    assert not any(t['id'] == id_b for t in search_a)

    search_b = search_transactions(query_text="", workspace_id=ws_b.id)
    assert any(t['id'] == id_b for t in search_b)
    assert not any(t['id'] == id_a for t in search_b)


def test_cross_workspace_mutation_protection():
    """Ensure that modifying or deleting a transaction from another workspace is rejected."""
    ws_a = get_or_create_workspace(chat_id="303030", chat_type="private", title="Tenant A", creator_user_id=301)
    ws_b = get_or_create_workspace(chat_id="404040", chat_type="private", title="Tenant B", creator_user_id=401)

    tx_a = Transaction(
        amount=750.0,
        transaction_type="SENT",
        person_name="Target A",
        workspace_id=ws_a.id
    )
    tx_a_id = insert_transaction(tx_a)

    # 1. Tenant B cannot look up Tenant A's transaction using scoped query
    assert get_transaction_by_id(tx_a_id, workspace_id=ws_b.id) is None

    # 2. Tenant B cannot update Tenant A's transaction
    updated = update_transaction(tx_a_id, {'amount': 999.0}, workspace_id=ws_b.id)
    assert updated is False
    # Verify amount was untouched
    tx_check = get_transaction_by_id(tx_a_id, workspace_id=ws_a.id)
    assert tx_check['amount'] == 750.0

    # 3. Tenant B cannot delete Tenant A's transaction
    deleted = delete_transaction(tx_a_id, workspace_id=ws_b.id)
    assert deleted is False
    # Verify row is still alive
    assert get_transaction_by_id(tx_a_id, workspace_id=ws_a.id) is not None


def test_cross_workspace_pending_receipts_isolation():
    """Ensure pending receipts in memory and database are isolated across workspaces."""
    ws_a_id = f"ws_pend_a_{uuid.uuid4().hex[:6]}"
    ws_b_id = f"ws_pend_b_{uuid.uuid4().hex[:6]}"

    t_pend = Transaction(
        amount=340.0,
        transaction_type="SENT",
        person_name="Coffee House",
        workspace_id=ws_a_id
    )
    p_id = f"p_{uuid.uuid4().hex[:8]}"

    # Save to Workspace A
    set_pending_transaction(p_id, t_pend, workspace_id=ws_a_id)

    # Workspace B should NOT find it
    assert fetch_pending_transaction(p_id, workspace_id=ws_b_id) is None

    # Workspace A finds it
    found = fetch_pending_transaction(p_id, workspace_id=ws_a_id)
    assert found is not None
    assert found.amount == 340.0

    # Pop from Workspace A
    popped = pop_pending_transaction(p_id, workspace_id=ws_a_id)
    assert popped is not None
    assert fetch_pending_transaction(p_id, workspace_id=ws_a_id) is None


def test_workspace_management_commands():
    """Verify /workspace, /members, and /setrole command execution and access control."""
    async def _test():
        chat_id = -99001
        owner_id = 991
        member_id = 992

        ws = get_or_create_workspace(
            chat_id=str(chat_id),
            chat_type="group",
            title="Alpha Team",
            creator_user_id=owner_id,
            username="alpha_boss",
            display_name="Boss"
        )
        add_workspace_member(ws.id, member_id, role="member", username="alpha_dev", display_name="Developer")

        context = MagicMock()
        context.args = []

        # 1. /workspace command
        up_ws = make_mock_update(user_id=owner_id, chat_id=chat_id, chat_type="group", text="/workspace")
        await workspace_command(up_ws, context)
        up_ws.message.reply_text.assert_called_once()
        reply = up_ws.message.reply_text.call_args[0][0]
        assert "Workspace Information" in reply
        assert "Alpha Team" in reply
        assert "Owner" in reply

        # 2. /members command
        up_members = make_mock_update(user_id=member_id, chat_id=chat_id, chat_type="group", text="/members")
        await members_command(up_members, context)
        up_members.message.reply_text.assert_called_once()
        mem_reply = up_members.message.reply_text.call_args[0][0]
        assert "Workspace Members" in mem_reply
        assert "@alpha_boss" in mem_reply or "Boss" in mem_reply
        assert "@alpha_dev" in mem_reply or "Developer" in mem_reply

        # 3. /setrole by non-owner is rejected
        up_setrole_fail = make_mock_update(user_id=member_id, chat_id=chat_id, chat_type="group", text=f"/setrole {owner_id} viewer")
        context.args = [str(owner_id), "viewer"]
        await setrole_command(up_setrole_fail, context)
        up_setrole_fail.message.reply_text.assert_called_once()
        reply_fail = up_setrole_fail.message.reply_text.call_args[0][0]
        assert any(phrase in reply_fail for phrase in ("Admin Only", "Only the workspace owner", "Permission Denied"))

        # 4. /setrole by owner succeeds
        up_setrole_success = make_mock_update(user_id=owner_id, chat_id=chat_id, chat_type="group", text=f"/setrole {member_id} admin")
        context.args = [str(member_id), "admin"]
        await setrole_command(up_setrole_success, context)
        up_setrole_success.message.reply_text.assert_called_once()
        success_reply = up_setrole_success.message.reply_text.call_args[0][0]
        assert "Role updated" in success_reply
        assert "ADMIN" in success_reply

        # Verify role persisted in DB
        updated_member = get_workspace_member(ws.id, member_id)
        assert updated_member.role == "admin"

    asyncio.run(_test())


def test_super_admin_emergency_access():
    """Verifies that user in SUPER_ADMIN_USER_IDS bypasses role checks as emergency owner."""
    async def _test():
        chat_id = -100998877
        super_admin_id = 999111888
        ws = get_or_create_workspace(chat_id, "group", "Emergency Test Group")

        with patch("config.SUPER_ADMIN_USER_IDS", [super_admin_id]):
            from bot.auth import is_super_admin, is_owner
            assert is_super_admin(super_admin_id) is True
            assert is_super_admin(12345) is False

            up = make_mock_update(user_id=super_admin_id, chat_id=chat_id, chat_type="group")
            assert is_owner(up) is True

            ctx = await resolve_workspace_context(up, required_policy="owner")
            assert ctx is not None
            assert ctx.role == "owner"

    asyncio.run(_test())


def test_cross_workspace_mutation_protection_default_ws():
    """Verifies that non-default workspace cannot mutate or delete transactions in default workspace."""
    from database.queries import get_default_workspace_id
    default_ws = get_default_workspace_id()

    # Create transaction in default workspace
    t_default = Transaction(
        amount=500.0,
        transaction_type="SENT",
        person_name="Default Owner",
        category="General",
        transaction_date="2026-10-08",
        workspace_id=default_ws
    )
    tx_id = insert_transaction(t_default)
    assert tx_id > 0

    # Create separate tenant workspace
    ws_tenant = get_or_create_workspace(chat_id=-100999888, chat_type="group", title="Tenant Group")

    # Attempt to delete default workspace transaction using tenant workspace scope
    del_res = delete_transaction(tx_id, workspace_id=ws_tenant.id)
    assert del_res is False, "Tenant workspace should not be able to delete default workspace transaction"

    # Verify transaction still exists and is untouched
    tx = get_transaction_by_id(tx_id, workspace_id=default_ws)
    assert tx is not None
    assert tx.get('deleted_at') is None

    # Attempt to update default workspace transaction using tenant workspace scope
    upd_res = update_transaction(tx_id, {'amount': 999.0}, workspace_id=ws_tenant.id)
    assert upd_res is False, "Tenant workspace should not be able to update default workspace transaction"

    # Transaction amount remains original
    tx_after = get_transaction_by_id(tx_id, workspace_id=default_ws)
    assert float(tx_after['amount']) == 500.0


def test_cross_workspace_undo_insert_protection():
    """Verifies that undo cannot delete transaction belonging to another workspace."""
    from services.undo_service import record_insert_action, perform_undo
    from database.queries import get_default_workspace_id

    ws_1 = get_or_create_workspace(chat_id=-100111222, chat_type="group", title="Workspace 1")
    ws_2 = get_or_create_workspace(chat_id=-100333444, chat_type="group", title="Workspace 2")

    t1 = Transaction(
        amount=250.0,
        transaction_type="SENT",
        person_name="Tenant 1 Payee",
        category="Food",
        transaction_date="2026-10-08",
        workspace_id=ws_1.id
    )
    tx1_id = insert_transaction(t1)
    tx1 = get_transaction_by_id(tx1_id, workspace_id=ws_1.id)
    assert tx1 is not None

    # Record insert for user 100 in ws_1
    record_insert_action(tx1['uid'], chat_id=-100111222, user_id=100, workspace_id=ws_1.id)

    # Attempt undo in ws_2 scope
    success, msg = perform_undo(chat_id=-100333444, user_id=100, workspace_id=ws_2.id)
    assert success is False

    # Original transaction in ws_1 is still live
    tx1_check = get_transaction_by_id(tx1_id, workspace_id=ws_1.id)
    assert tx1_check is not None
    assert tx1_check.get('deleted_at') is None

