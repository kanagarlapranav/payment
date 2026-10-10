import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from database.db import setup_database, get_db_connection
from database.models import Transaction
from database.queries import (
    get_default_workspace_id,
    get_or_create_workspace,
    add_workspace_member,
    insert_transaction,
    get_transactions_paginated,
    ensure_all_user_workspaces,
    get_transaction_by_id,
)
from bot.auth import get_workspace_context, set_user_active_workspace, clear_user_active_workspace_cache
from bot.commands import render_home_menu_text
from scripts.repair_workspace_provenance import repair_provenance


def make_mock_update(user_id: int, chat_id: int, chat_type: str = "private", text: str = ""):
    from telegram import Update, User, Chat, Message
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
    msg.from_user = user
    msg.chat = chat
    update.message = msg
    update.effective_message = msg
    return update


class TestWorkspaceCollisionFixes(unittest.TestCase):
    def setUp(self):
        setup_database()
        clear_user_active_workspace_cache()

    # --- Fix A Tests ---
    def test_fix_a_admin_private_dm_routes_to_personal_workspace_not_group(self):
        """
        Verify that an admin of the default workspace in a private DM (without explicit switch)
        routes to their own personal workspace, so transactions do NOT land in the group ledger.
        """
        owner_id = 9990001
        admin_id = 8880002
        default_ws_id = get_default_workspace_id()

        # Add admin_id with role 'admin' in default group workspace
        add_workspace_member(default_ws_id, admin_id, role="admin")

        with patch('config.TELEGRAM_USER_ID', str(owner_id)):
            # 1. Admin in private DM (unswitched)
            up_admin_dm = make_mock_update(user_id=admin_id, chat_id=admin_id, chat_type="private")
            ctx_admin = get_workspace_context(up_admin_dm)
            self.assertIsNotNone(ctx_admin)
            # Must NOT route to group/default workspace
            self.assertNotEqual(ctx_admin.workspace_id, default_ws_id)
            admin_personal_ws_id = ctx_admin.workspace_id

            # Admin records a transaction in DM
            tx = Transaction(
                amount=150.0,
                transaction_type="SENT",
                person_name="Personal Coffee",
                category="Food & Dining",
                transaction_date="2026-10-10",
                workspace_id=admin_personal_ws_id,
                telegram_user_id=admin_id,
                telegram_chat_id=admin_id
            )
            insert_transaction(tx)

            # Row does NOT appear in group workspace history
            group_history = get_transactions_paginated(workspace_id=default_ws_id)
            group_payees = [t['person_name'] for t in group_history['transactions']]
            self.assertNotIn("Personal Coffee", group_payees)

            # Row DOES appear in admin's personal DM workspace history
            dm_history = get_transactions_paginated(workspace_id=admin_personal_ws_id)
            dm_payees = [t['person_name'] for t in dm_history['transactions']]
            self.assertIn("Personal Coffee", dm_payees)

    def test_fix_a_admin_switched_to_group_records_into_group(self):
        """
        Verify that when the same admin explicitly switches to the group ledger in DM,
        transactions land in the group workspace.
        """
        owner_id = 9990001
        admin_id = 8880002
        default_ws_id = get_default_workspace_id()

        add_workspace_member(default_ws_id, admin_id, role="admin")

        with patch('config.TELEGRAM_USER_ID', str(owner_id)):
            set_user_active_workspace(admin_id, default_ws_id)

            up_admin_dm = make_mock_update(user_id=admin_id, chat_id=admin_id, chat_type="private")
            ctx_switched = get_workspace_context(up_admin_dm)
            self.assertIsNotNone(ctx_switched)
            self.assertEqual(ctx_switched.workspace_id, default_ws_id)

            tx = Transaction(
                amount=500.0,
                transaction_type="SENT",
                person_name="Group Lunch Expense",
                category="Food & Dining",
                transaction_date="2026-10-10",
                workspace_id=ctx_switched.workspace_id,
                telegram_user_id=admin_id
            )
            insert_transaction(tx)

            group_history = get_transactions_paginated(workspace_id=default_ws_id)
            group_payees = [t['person_name'] for t in group_history['transactions']]
            self.assertIn("Group Lunch Expense", group_payees)

    def test_fix_a_global_owner_dm_behavior_unchanged(self):
        """Verify that global owner's private DM continues defaulting to the default workspace."""
        owner_id = 9990001
        default_ws_id = get_default_workspace_id()

        with patch('config.TELEGRAM_USER_ID', str(owner_id)):
            up_owner = make_mock_update(user_id=owner_id, chat_id=owner_id, chat_type="private")
            ctx_owner = get_workspace_context(up_owner)
            self.assertIsNotNone(ctx_owner)
            self.assertEqual(ctx_owner.workspace_id, default_ws_id)

    # --- Fix B Tests ---
    def test_fix_b_stop_silently_rehoming_orphaned_rows(self):
        """Verify ensure_all_user_workspaces does not re-home orphaned rows and logs a warning."""
        # Insert a transaction with NULL workspace_id
        tx = Transaction(
            amount=75.0,
            transaction_type="SENT",
            person_name="Orphaned Bakery",
            transaction_date="2026-10-10",
            workspace_id=None
        )
        tx_id = insert_transaction(tx)
        with get_db_connection() as conn:
            conn.cursor().execute("UPDATE transactions SET workspace_id = NULL WHERE id = ?", (tx_id,))
            conn.commit()

        # Call ensure_all_user_workspaces with logger spy
        with patch('database.queries.logger.warning') as mock_warn:
            ensure_all_user_workspaces()
            # Assert warning was logged
            warning_called = any("orphaned rows detected, left in place for manual repair" in str(c) for c in mock_warn.call_args_list)
            self.assertTrue(warning_called, "Expected warning about orphaned rows")

        # Assert row's workspace_id is untouched (still None)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT workspace_id FROM transactions WHERE id = ?", (tx_id,))
            row = cur.fetchone()
            self.assertIsNone(row['workspace_id'])

    # --- Fix C Tests ---
    def test_fix_c_home_menu_active_ledger_indicator(self):
        """Verify home card displays active ledger title and [Switched] indicator when switched."""
        ws = get_or_create_workspace(chat_id=-100888999, chat_type="group", title="Finance Dept")

        # Unswitched: shows plain title
        text_unswitched = render_home_menu_text(workspace_id=ws.id, switched=False)
        self.assertIn("🏢 <b>Ledger:</b> Finance Dept", text_unswitched)
        self.assertNotIn("[Switched]", text_unswitched)

        # Switched: shows title + [Switched]
        text_switched = render_home_menu_text(workspace_id=ws.id, switched=True)
        self.assertIn("🏢 <b>Ledger:</b> Finance Dept <i>[Switched]</i>", text_switched)

        # Unknown workspace_id degrades gracefully to "Workspace"
        text_unknown = render_home_menu_text(workspace_id="non-existent-ws-id", switched=False)
        self.assertIn("🏢 <b>Ledger:</b> Workspace", text_unknown)

    # --- Fix D Tests ---
    def test_fix_d_repair_workspace_provenance(self):
        """
        Verify scripts/repair_workspace_provenance.py logic on a temp SQLite database:
        - single match re-tags to originating workspace
        - dry-run writes nothing
        - ambiguous chat (>1 workspace) is skipped
        - origin-less row is skipped
        """
        temp_fd, temp_db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(temp_fd)

        try:
            conn = sqlite3.connect(temp_db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Create tables
            cur.execute("""
                CREATE TABLE workspaces (
                    id TEXT PRIMARY KEY,
                    chat_id INTEGER,
                    chat_type TEXT,
                    title TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            cur.execute("""
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT,
                    telegram_chat_id TEXT,
                    person_name TEXT,
                    amount REAL,
                    transaction_date TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE undo_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT,
                    chat_id INTEGER,
                    action TEXT,
                    uid TEXT
                )
            """)

            # Populate workspaces
            # 1. User 101 DM
            cur.execute("INSERT INTO workspaces (id, chat_id, title, is_active) VALUES ('ws_dm_101', 101, 'User 101 DM', 1)")
            # 2. Shared group
            cur.execute("INSERT INTO workspaces (id, chat_id, title, is_active) VALUES ('ws_group', -100200, 'Payment Group', 1)")
            # 3 & 4. Ambiguous chat_id=303
            cur.execute("INSERT INTO workspaces (id, chat_id, title, is_active) VALUES ('ws_ambig_1', 303, 'Ambig 1', 1)")
            cur.execute("INSERT INTO workspaces (id, chat_id, title, is_active) VALUES ('ws_ambig_2', 303, 'Ambig 2', 1)")

            # Populate transactions:
            # tx1: misplaced row (written in chat 101, but stamped ws_group)
            cur.execute("INSERT INTO transactions (id, workspace_id, telegram_chat_id, person_name) VALUES (1, 'ws_group', '101', 'Misplaced Tea')")
            # tx2: correctly matched row
            cur.execute("INSERT INTO transactions (id, workspace_id, telegram_chat_id, person_name) VALUES (2, 'ws_group', '-100200', 'Group Food')")
            # tx3: ambiguous chat
            cur.execute("INSERT INTO transactions (id, workspace_id, telegram_chat_id, person_name) VALUES (3, 'ws_group', '303', 'Ambig Tx')")
            # tx4: origin-less row
            cur.execute("INSERT INTO transactions (id, workspace_id, telegram_chat_id, person_name) VALUES (4, 'ws_group', NULL, 'Originless Tx')")

            # Populate undo_log:
            # u1: misplaced undo
            cur.execute("INSERT INTO undo_log (id, workspace_id, chat_id, action, uid) VALUES (10, 'ws_group', 101, 'insert', 'uid_10')")

            conn.commit()

            # Test 1: Dry run (apply=False)
            dry_res = repair_provenance(conn_or_db_path=temp_db_path, apply=False)
            self.assertEqual(dry_res['transactions']['retagged'], 1)
            self.assertEqual(dry_res['transactions']['skipped']['already_matched'], 1)
            self.assertEqual(dry_res['transactions']['skipped']['ambiguous_matching_workspaces'], 1)
            self.assertEqual(dry_res['transactions']['skipped']['origin_less'], 1)
            self.assertEqual(dry_res['undo_log']['retagged'], 1)

            # In dry-run, DB must NOT be modified
            cur.execute("SELECT workspace_id FROM transactions WHERE id = 1")
            self.assertEqual(cur.fetchone()[0], 'ws_group')
            cur.execute("SELECT workspace_id FROM undo_log WHERE id = 10")
            self.assertEqual(cur.fetchone()[0], 'ws_group')

            # Test 2: Apply (apply=True)
            apply_res = repair_provenance(conn_or_db_path=temp_db_path, apply=True)
            self.assertEqual(apply_res['transactions']['retagged'], 1)
            self.assertEqual(apply_res['undo_log']['retagged'], 1)

            # In apply mode, misplaced rows are re-tagged
            cur.execute("SELECT workspace_id FROM transactions WHERE id = 1")
            self.assertEqual(cur.fetchone()[0], 'ws_dm_101')
            cur.execute("SELECT workspace_id FROM undo_log WHERE id = 10")
            self.assertEqual(cur.fetchone()[0], 'ws_dm_101')

            # Other rows remain untouched
            cur.execute("SELECT workspace_id FROM transactions WHERE id = 2")
            self.assertEqual(cur.fetchone()[0], 'ws_group')
            cur.execute("SELECT workspace_id FROM transactions WHERE id = 3")
            self.assertEqual(cur.fetchone()[0], 'ws_group')
            cur.execute("SELECT workspace_id FROM transactions WHERE id = 4")
            self.assertEqual(cur.fetchone()[0], 'ws_group')

            conn.close()
        finally:
            if os.path.exists(temp_db_path):
                os.remove(temp_db_path)


if __name__ == '__main__':
    unittest.main()
