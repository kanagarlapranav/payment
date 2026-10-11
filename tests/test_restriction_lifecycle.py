import asyncio
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

import config
from database.models import Workspace
from database.queries import (
    get_or_create_workspace, add_workspace_member, get_workspace_member,
    set_workspace_setting, get_workspace_setting, set_user_permission_and_role,
    ensure_all_user_workspaces, get_default_workspace_id
)
from bot.auth import get_workspace_context, is_owner
from bot.handlers import handle_callback_query
from bot.commands import setrole_command, admin_command


class TestRestrictionLifecycle(unittest.TestCase):

    def setUp(self):
        self.owner_id = int(config.TELEGRAM_USER_ID) if config.TELEGRAM_USER_ID else 123456789
        self.target_uid = 7788990011

    def test_bug_1_unrestrict_reports_unchanged_role_and_workspaces(self):
        """Bug 1: Unrestricting a user via set_access_remove must not silently auto-promote,

        and the confirmation message must explicitly state their unchanged role and workspaces."""
        ws = get_or_create_workspace(
            title="Alpha Workspace",
            creator_user_id=self.owner_id,
            chat_id=111222333,
            chat_type="group"
        )
        # 1. Restrict target user in this workspace
        set_workspace_setting(ws.id, 'restricted_user_ids', str(self.target_uid))

        # 2. Provision target user as member
        add_workspace_member(ws.id, self.target_uid, role="member")
        mem = get_workspace_member(ws.id, self.target_uid)
        self.assertEqual(mem.role, "member")

        # 3. Simulate owner tapping remove restriction button (set_access_remove) in that workspace
        from bot.auth import set_user_active_workspace
        set_user_active_workspace(self.owner_id, ws.id)
        try:
            update = MagicMock()
            update.effective_user.id = self.owner_id
            update.effective_chat.id = self.owner_id
            update.effective_chat.type = "private"

            query = MagicMock()
            query.data = f"set_access_remove:{self.target_uid}"
            query.from_user.id = self.owner_id
            query.message = MagicMock()
            query.answer = AsyncMock()
            query.edit_message_text = AsyncMock()
            update.callback_query = query
            context = MagicMock()

            asyncio.run(handle_callback_query(update, context))

            # 4. Verify restriction is removed
            from config import is_restricted_user
            self.assertFalse(is_restricted_user(self.target_uid, workspace_id=ws.id))

            # 5. Verify role remains 'member' (NOT auto-promoted)
            mem_after = get_workspace_member(ws.id, self.target_uid)
            self.assertEqual(mem_after.role, "member")

            # 6. Verify confirmation text explicitly names unchanged role and workspace
            self.assertTrue(query.edit_message_text.called)
            call_text = query.edit_message_text.call_args[0][0]
            self.assertIn(str(self.target_uid), call_text)
            self.assertIn("unchanged", call_text.lower())
            self.assertIn("member", call_text.lower())
            self.assertIn("Alpha Workspace", call_text)
        finally:
            set_user_active_workspace(self.owner_id, None)

    def test_bug_2_no_hardcoded_user_in_default_restricted_list(self):
        """Bug 2: With RESTRICTED_USER_IDS unset in env and no DB row, no real user is restricted by default."""
        with patch.dict(os.environ, {}, clear=False):
            if "RESTRICTED_USER_IDS" in os.environ:
                del os.environ["RESTRICTED_USER_IDS"]
            # When re-evaluating or checking raw defaults
            from config import is_restricted_user, get_restricted_user_ids
            # Real user 8343764796 must NOT be in default list
            fresh_list = get_restricted_user_ids(workspace_id="nonexistent_ws_for_test")
            self.assertEqual(fresh_list, [])
            self.assertFalse(is_restricted_user(8343764796, workspace_id="nonexistent_ws_for_test"))

    def test_bug_3_restriction_checks_respect_workspace_scoping(self):
        """Bug 3: is_restricted_user and get_workspace_context must be scoped to the workspace without bleed."""
        ws_a = get_or_create_workspace(title="Workspace A", creator_user_id=self.owner_id, chat_id=222333444, chat_type="group")
        ws_b = get_or_create_workspace(title="Workspace B", creator_user_id=self.owner_id, chat_id=333444555, chat_type="group")

        # Restrict in ws_a only
        set_workspace_setting(ws_a.id, 'restricted_user_ids', str(self.target_uid))
        set_workspace_setting(ws_b.id, 'restricted_user_ids', "")
        add_workspace_member(ws_a.id, self.target_uid, role="member")

        from config import is_restricted_user
        self.assertTrue(is_restricted_user(self.target_uid, workspace_id=ws_a.id))
        self.assertFalse(is_restricted_user(self.target_uid, workspace_id=ws_b.id))

        # Check get_workspace_context for DM workspace: user restricted in default workspace
        # but not restricted in their DM workspace
        def_ws_id = get_default_workspace_id()
        set_workspace_setting(def_ws_id, 'restricted_user_ids', str(self.target_uid))

        dm_update = MagicMock()
        dm_update.effective_user.id = self.target_uid
        dm_update.effective_user.username = "targetuser"
        dm_update.effective_user.full_name = "Target User"
        dm_update.effective_chat.id = self.target_uid
        dm_update.effective_chat.type = "private"
        dm_update.effective_chat.title = None

        dm_ctx = get_workspace_context(dm_update)
        self.assertIsNotNone(dm_ctx)
        # In their private DM workspace, they should NOT be restricted, so role should be 'owner'
        self.assertEqual(dm_ctx.role, "owner")

    def test_bug_4_startup_sweep_does_not_revert_unrestricted_owner(self):
        """Bug 4: ensure_all_user_workspaces must not demote an owner unless restricted in THAT workspace."""
        ws = get_or_create_workspace(title="Sweep Test WS", creator_user_id=self.owner_id, chat_id=444555666, chat_type="group")
        # Explicitly unrestrict in this workspace
        set_workspace_setting(ws.id, 'restricted_user_ids', "")

        # Set user role to owner
        set_user_permission_and_role(self.target_uid, "owner", is_active=True, workspace_id=ws.id)
        mem = get_workspace_member(ws.id, self.target_uid)
        self.assertEqual(mem.role, "owner")

        # Run ensure_all_user_workspaces
        ensure_all_user_workspaces()

        # Must still be owner!
        mem_after = get_workspace_member(ws.id, self.target_uid)
        self.assertEqual(mem_after.role, "owner")

        # Now restrict in this workspace
        set_workspace_setting(ws.id, 'restricted_user_ids', str(self.target_uid))
        ensure_all_user_workspaces()

        # Must now be demoted to member
        mem_demoted = get_workspace_member(ws.id, self.target_uid)
        self.assertEqual(mem_demoted.role, "member")

    def test_bug_5_no_hardcoded_username_fallbacks_in_setrole_and_admin(self):
        """Bug 5: /setrole nagendra and /admin nagendra when no member matches must return not found."""
        ws = get_or_create_workspace(title="Commands WS", creator_user_id=self.owner_id, chat_id=555666777, chat_type="group")

        update = MagicMock()
        update.effective_user.id = self.owner_id
        update.effective_chat.id = 555666777
        update.effective_chat.type = "group"
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.message.reply_to_message = None

        context = MagicMock()
        context.args = ["nagendra", "admin"]

        with patch('bot.auth.get_workspace_context') as mock_ctx:
            mock_ctx.return_value = MagicMock(workspace_id=ws.id, role="owner")
            # Run /setrole nagendra admin
            asyncio.run(setrole_command(update, context))
            reply_text = update.message.reply_text.call_args[0][0]
            self.assertIn("not found", reply_text.lower())

            # Run /admin nagendra
            update.message.reply_text.reset_mock()
            context.args = ["nagendra"]
            asyncio.run(admin_command(update, context))
            reply_text_admin = update.message.reply_text.call_args[0][0]
            # Should show admin list and NOT assign role to hardcoded UID
            self.assertNotIn("Promoted", reply_text_admin)

    def test_bug_6_no_hardcoded_personal_names_in_workspace_auto_title(self):
        """Bug 6: Auto-titling a workspace for 8343764796 uses generic path, not hardcoded personal name."""
        from database.db import get_db_connection
        # Create a workspace member or access request for 8343764796 without username
        hardcoded_uid = 8343764796
        def_ws_id = get_default_workspace_id()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM workspaces WHERE chat_id = ?", (hardcoded_uid,))
            cursor.execute("""
                INSERT OR REPLACE INTO workspace_members (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
                VALUES (?, ?, '', '', 'member', 1, '2026-01-01', '2026-01-01')
            """, (def_ws_id, hardcoded_uid))
            conn.commit()

        ensure_all_user_workspaces()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title FROM workspaces WHERE chat_id = ? AND is_active = 1", (hardcoded_uid,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            title = row['title']
            # Title should be f"User {hardcoded_uid} (Personal)", never hardcoded "Nagendra"
            self.assertNotIn("Nagendra", title)
            self.assertIn(str(hardcoded_uid), title)


if __name__ == '__main__':
    unittest.main()
