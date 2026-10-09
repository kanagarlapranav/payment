import unittest
import html
from database.db import get_db_connection
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance, get_transaction_by_id, get_transactions_paginated,
    get_payee_category, remember_payee_category, get_default_workspace_id
)
from bot.keyboards import (
    get_home_menu_keyboard, get_add_menu_keyboard, get_more_menu_keyboard,
    get_transaction_detail_keyboard, get_backup_status_keyboard,
    get_history_paginated_keyboard, get_quick_add_keyboard,
    get_confirmation_card_keyboard, get_edit_pending_fields_keyboard,
    get_help_keyboard, get_balance_keyboard, get_stats_keyboard,
    get_budget_keyboard, get_insights_keyboard, get_digest_keyboard,
    get_cafestats_keyboard, get_standard_nav_keyboard, get_delete_confirmed_keyboard
)
from bot.commands import (
    render_home_menu_text, render_history_page, render_transaction_detail,
    render_backup_status_text
)
from utils.currency import format_currency

class TestUXAndNavigation(unittest.TestCase):

    def test_keyboards_callback_data_length_under_64_bytes(self):
        """Ensure all callback_data in every UX keyboard is strictly <= 64 bytes."""
        keyboards = [
            get_home_menu_keyboard(),
            get_add_menu_keyboard(),
            get_more_menu_keyboard(),
            get_transaction_detail_keyboard(12345),
            get_backup_status_keyboard(),
            get_history_paginated_keyboard(page=2, total_pages=5, filter_type="SENT", tx_rows=[{"id": 101}, {"id": 102}]),
            get_quick_add_keyboard(top_payees=[{"person_name": "Swiggy"}, {"person_name": "Zomato"}]),
            get_confirmation_card_keyboard(pending_id="abc123def4", duplicate_warning=True),
            get_edit_pending_fields_keyboard(pending_id="abc123def4"),
            get_help_keyboard(),
            get_balance_keyboard(),
            get_stats_keyboard(),
            get_budget_keyboard(),
            get_insights_keyboard(),
            get_digest_keyboard(),
            get_cafestats_keyboard(),
            get_standard_nav_keyboard(),
            get_delete_confirmed_keyboard()
        ]
        
        for kb in keyboards:
            for row in kb.inline_keyboard:
                for btn in row:
                    cb = btn.callback_data
                    if cb:
                        self.assertLessEqual(
                            len(cb.encode('utf-8')),
                            64,
                            f"callback_data '{cb}' exceeds 64 bytes ({len(cb.encode('utf-8'))} bytes)"
                        )

    def test_indian_currency_formatting(self):
        """Verify Indian number format formatting (e.g. ₹1,23,456)."""
        self.assertEqual(format_currency(123456), "₹1,23,456")
        self.assertEqual(format_currency(1000), "₹1,000")
        self.assertEqual(format_currency(50), "₹50")
        self.assertEqual(format_currency(10000000), "₹1,00,00,000")

    def test_history_pagination_and_empty_states(self):
        """Test history pagination rendering, page navigation, and empty states."""
        # 1. Non-empty history
        text, markup = render_history_page(page=1, filter_type="ALL", page_size=5)
        self.assertIn("🧾", text)
        self.assertIsNotNone(markup)

        # 2. Empty state on nonexistent page / unusual filter
        empty_text, empty_markup = render_history_page(page=99999, filter_type="RECEIVED", page_size=10)
        self.assertIn("📭", empty_text)
        self.assertIn("No transactions found for this filter.", empty_text)
        self.assertIsNotNone(empty_markup)

        # 3. Boundary check: page <= 1 (page 0 or -1 clamps safely to page 1)
        zero_text, zero_markup = render_history_page(page=0, filter_type="ALL", page_size=5)
        self.assertIn("🧾", zero_text)
        self.assertIn("Page 1 of", zero_text)

        # 4. Keyboard boundary verification
        kb_page1 = get_history_paginated_keyboard(page=1, total_pages=3)
        cb_p1 = [b.callback_data for r in kb_page1.inline_keyboard for b in r]
        self.assertFalse(any("nav:history:0:" in c for c in cb_p1), "Page 1 keyboard should not have Prev link to page 0")
        self.assertTrue(any("nav:history:2:" in c for c in cb_p1), "Page 1 of 3 should have Next link to page 2")

        kb_page3 = get_history_paginated_keyboard(page=3, total_pages=3)
        cb_p3 = [b.callback_data for r in kb_page3.inline_keyboard for b in r]
        self.assertFalse(any("nav:history:4:" in c for c in cb_p3), "Page 3 of 3 keyboard should not have Next link to page 4")
        self.assertTrue(any("nav:history:2:" in c for c in cb_p3), "Page 3 of 3 should have Prev link to page 2")


    def test_transaction_detail_screen(self):
        """Test transaction detail view rendering and keyboard actions."""
        # Insert a sample transaction
        import uuid
        unique_ref = f"UPI{uuid.uuid4().hex[:12].upper()}"
        t = Transaction(
            amount=450.0,
            transaction_type="SENT",
            person_name="Tea Post Cafe",
            category="Food & Snacks",
            transaction_date="2026-09-21",
            reference_number=unique_ref
        )
        tx_id = insert_transaction_with_balance(t)
        self.assertIsNotNone(tx_id)

        # Render detail
        detail_text, detail_markup = render_transaction_detail(tx_id)
        self.assertIn(f"Transaction Details #{tx_id}", detail_text)
        self.assertIn("Tea Post Cafe", detail_text)
        self.assertIn("Food &amp; Snacks" if "&amp;" in detail_text else "Food & Snacks", detail_text)
        self.assertIn("₹450", detail_text)
        self.assertIn(unique_ref, detail_text)

        # Verify buttons
        callbacks = [btn.callback_data for row in detail_markup.inline_keyboard for btn in row]
        self.assertIn(f"edit_tx:{tx_id}", callbacks)
        self.assertIn(f"delete_tx:{tx_id}", callbacks)
        self.assertIn(f"dup_tx:{tx_id}", callbacks)
        self.assertIn("nav:history:1:ALL", callbacks)

        # Nonexistent transaction
        missing_text, _ = render_transaction_detail(99999999)
        self.assertIn("❌", missing_text)
        self.assertIn("Transaction not found or deleted", missing_text)

    def test_receipt_confirmation_card_and_edit_fields(self):
        """Test receipt confirmation card buttons and field editing buttons."""
        pending_id = "test_pid_01"
        kb_confirm = get_confirmation_card_keyboard(pending_id, duplicate_warning=True)
        callbacks_confirm = [btn.callback_data for row in kb_confirm.inline_keyboard for btn in row]
        
        self.assertIn(f"save_p:{pending_id}", callbacks_confirm)
        self.assertIn(f"edit_p:{pending_id}", callbacks_confirm)
        self.assertIn(f"cat_p:{pending_id}", callbacks_confirm)
        self.assertIn(f"cancel_p:{pending_id}", callbacks_confirm)

        kb_edit = get_edit_pending_fields_keyboard(pending_id)
        callbacks_edit = [btn.callback_data for row in kb_edit.inline_keyboard for btn in row]
        self.assertIn(f"ep_field:{pending_id}:amount", callbacks_edit)
        self.assertIn(f"ep_field:{pending_id}:person", callbacks_edit)
        self.assertIn(f"ep_field:{pending_id}:date", callbacks_edit)
        self.assertIn(f"ep_field:{pending_id}:type", callbacks_edit)
        self.assertIn(f"ep_back:{pending_id}", callbacks_edit)

    def test_payee_category_memory(self):
        """Verify that payee-to-category choices are remembered and retrieved."""
        remember_payee_category("Blue Tokai Coffee", "Food & Beverages")
        cat = get_payee_category("Blue Tokai Coffee")
        self.assertEqual(cat, "Food & Beverages")

    def test_backup_status_screen(self):
        """Test backup status text and actions."""
        text = render_backup_status_text()
        markup = get_backup_status_keyboard()
        self.assertIn("Backup &amp; Disaster Recovery Status" if "&amp;" in text else "Backup & Disaster Recovery Status", text)
        self.assertIn("Last Local Backup:", text)
        self.assertIn("Database Revision:", text)
        self.assertIn("Status:", text)
        
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("backup_now", callbacks)
        self.assertIn("nav:restore_info", callbacks)
        self.assertIn("nav:home", callbacks)

    def test_command_keyboards_structure(self):
        """Verify that every command keyboard exposes expected navigation actions."""
        help_callbacks = [btn.callback_data for row in get_help_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:home", help_callbacks)
        self.assertIn("nav:history:1:ALL", help_callbacks)
        self.assertIn("nav:balance", help_callbacks)
        self.assertIn("nav:today", help_callbacks)
        self.assertIn("nav:stats", help_callbacks)
        self.assertIn("nav:budget", help_callbacks)
        self.assertIn("nav:gemini", help_callbacks)
        self.assertIn("nav:dash_info", help_callbacks)

        balance_callbacks = [btn.callback_data for row in get_balance_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:today", balance_callbacks)
        self.assertIn("nav:history:1:ALL", balance_callbacks)
        self.assertIn("nav:stats", balance_callbacks)
        self.assertIn("nav:quickadd", balance_callbacks)
        self.assertIn("nav:home", balance_callbacks)

        stats_callbacks = [btn.callback_data for row in get_stats_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:budget", stats_callbacks)
        self.assertIn("nav:insights", stats_callbacks)
        self.assertIn("nav:home", stats_callbacks)

        budget_callbacks = [btn.callback_data for row in get_budget_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:stats", budget_callbacks)
        self.assertIn("nav:insights", budget_callbacks)
        self.assertIn("nav:home", budget_callbacks)

        insights_callbacks = [btn.callback_data for row in get_insights_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:stats", insights_callbacks)
        self.assertIn("nav:budget", insights_callbacks)
        self.assertIn("nav:home", insights_callbacks)

        digest_callbacks = [btn.callback_data for row in get_digest_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:today", digest_callbacks)
        self.assertIn("nav:stats", digest_callbacks)
        self.assertIn("nav:home", digest_callbacks)

        cafe_callbacks = [btn.callback_data for row in get_cafestats_keyboard().inline_keyboard for btn in row]
        self.assertIn("cafe_view_menu", cafe_callbacks)
        self.assertIn("cafe_edit_last", cafe_callbacks)
        self.assertIn("nav:home", cafe_callbacks)

        nav_callbacks = [btn.callback_data for row in get_standard_nav_keyboard().inline_keyboard for btn in row]
        self.assertIn("nav:history:1:ALL", nav_callbacks)
        self.assertIn("nav:home", nav_callbacks)

    def test_pending_receipt_persistence_and_restart_resilience(self):
        """Verify that pending receipts survive memory cache clearance (bot restart/deploy)."""
        from bot.handlers import (
            set_pending_transaction, fetch_pending_transaction, pop_pending_transaction,
            pending_transactions
        )
        import uuid
        from datetime import date

        pid = f"test_{uuid.uuid4().hex[:8]}"
        tx = Transaction(
            amount=250.0,
            transaction_type="SENT",
            person_name="Nagendra Test",
            category="Food & Dining",
            transaction_date=date(2026, 9, 23),
            transaction_time="08:58 AM",
            bank_name="Union Bank Of India",
            reference_number="8233"
        )
        set_pending_transaction(pid, tx)

        # Simulate bot reboot/restart wiping memory
        pending_transactions.clear()
        self.assertNotIn(pid, pending_transactions)

        # Fetch should restore from SQLite
        restored_tx = fetch_pending_transaction(pid)
        self.assertIsNotNone(restored_tx)
        self.assertEqual(restored_tx.amount, 250.0)
        self.assertEqual(restored_tx.person_name, "Nagendra Test")
        self.assertEqual(restored_tx.category, "Food & Dining")
        self.assertEqual(restored_tx.reference_number, "8233")

        # Pop should remove from both memory and DB
        popped = pop_pending_transaction(pid)
        self.assertIsNotNone(popped)
        self.assertIsNone(fetch_pending_transaction(pid))

    def test_reconstruct_transaction_from_card_exact_video(self):
        """Verify fallback parsing of the exact confirmation card shown in the user's video."""
        from bot.handlers import reconstruct_transaction_from_card

        # Text format directly from receipt card in user's video
        card_text = (
            "🧾 <b>Payment detected</b>\n"
            "━━━━━━━━━━━━━━\n"
            "💸 <b>₹1</b> → <b>Narise Nagendra</b>\n"
            "🏷 General   📅 23 Sep, 08:58 AM\n"
            "🏦 Union Bank Of India · Ref …8233"
        )
        reconstructed = reconstruct_transaction_from_card(card_text)
        self.assertIsNotNone(reconstructed)
        self.assertEqual(reconstructed.amount, 1.0)
        self.assertEqual(reconstructed.transaction_type, "SENT")
        self.assertEqual(reconstructed.person_name, "Narise Nagendra")
        self.assertEqual(reconstructed.category, "General")
        self.assertEqual(reconstructed.bank_name, "Union Bank Of India")
        self.assertEqual(reconstructed.reference_number, "8233")
        self.assertEqual(reconstructed.transaction_time, "08:58 AM")

    def test_already_saved_clear_message_and_force_save(self):
        """Verify that attempting to save a duplicate transaction shows clear Already Saved details."""
        from unittest.mock import AsyncMock, MagicMock
        from bot.handlers import handle_callback_query, set_pending_transaction
        import uuid
        from datetime import date
        from services.transaction_service import commit_transaction
        from config import TELEGRAM_USER_ID

        ref_no = f"DUP_REF_{uuid.uuid4().hex[:6]}"
        tx = Transaction(
            amount=1.0,
            transaction_type="SENT",
            person_name="Narise Nagendra",
            category="General",
            transaction_date=date(2026, 9, 23),
            transaction_time="08:58 AM",
            bank_name="Union Bank Of India",
            reference_number=ref_no
        )
        saved = commit_transaction(tx)
        self.assertTrue(saved)
        self.assertIsNotNone(tx.id)

        # Now simulate user tapping save_p again on a receipt card for this same transaction
        pid = f"pid_{uuid.uuid4().hex[:6]}"
        tx_duplicate = Transaction(
            amount=1.0,
            transaction_type="SENT",
            person_name="Narise Nagendra",
            category="General",
            transaction_date=date(2026, 9, 23),
            transaction_time="08:58 AM",
            bank_name="Union Bank Of India",
            reference_number=ref_no
        )
        set_pending_transaction(pid, tx_duplicate)

        update = MagicMock()
        query = MagicMock()
        query.data = f"save_p:{pid}"
        owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else 12345
        query.from_user.id = owner_id
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        import asyncio
        from unittest.mock import patch
        with patch('config.TELEGRAM_USER_ID', owner_id):
            asyncio.run(handle_callback_query(update, context))

        query.edit_message_text.assert_called_once()
        call_text = query.edit_message_text.call_args[0][0]
        call_markup = query.edit_message_text.call_args[1].get('reply_markup')
        
        # Verify it clearly states Already Saved with the ID and details
        self.assertIn("Already Saved!", call_text)
        self.assertIn(f"#{tx.id}", call_text)
        self.assertIn("Narise Nagendra", call_text)

        # Verify buttons include Save as New Entry and Undo
        callbacks = [btn.callback_data for row in call_markup.inline_keyboard for btn in row]
        self.assertIn(f"force_save_p:{pid}", callbacks)
        self.assertIn(f"undo_tx:{tx.id}", callbacks)

    def test_delete_confirm_flow_acknowledgement_and_text(self):
        """Verify tapping Confirm Delete acknowledges immediately, edits text to 'Delete Confirmed!', and updates balance."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        from config import TELEGRAM_USER_ID

        tx = Transaction(
            amount=1.0,
            transaction_type="SENT",
            person_name="Narise Nagendra",
            transaction_date="2026-09-23",
            category="General"
        )
        tx_id = insert_transaction_with_balance(tx)

        update = MagicMock()
        query = MagicMock()
        query.data = f"delete_confirm:{tx_id}"
        owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else 12345
        query.from_user.id = owner_id
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"
        query.message.text = "Delete Transaction"
        query.edit_message_text = AsyncMock()
        query.answer = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        import asyncio
        with patch('config.TELEGRAM_USER_ID', owner_id), \
             patch('services.backup_service.backup_to_telegram', AsyncMock(return_value=True)) as mock_bkp, \
             patch('services.task_manager.schedule_debounced_backup') as mock_sched:
            asyncio.run(handle_callback_query(update, context))

        # Assert no auto-backups were pushed to Telegram
        mock_bkp.assert_not_called()
        mock_sched.assert_not_called()

        # 1. Immediate answer called with "✅ Delete Confirmed!"
        query.answer.assert_called_with("✅ Delete Confirmed!", show_alert=False)

        # 2. Text contains "Delete Confirmed!"
        query.edit_message_text.assert_called_once()
        call_text = query.edit_message_text.call_args[0][0]
        call_markup = query.edit_message_text.call_args[1].get('reply_markup')

        self.assertIn("Delete Confirmed!", call_text)
        self.assertIn(f"Transaction #{tx_id} has been deleted", call_text)
        self.assertIn("Narise Nagendra", call_text)
        self.assertIn("Updated Current Balance", call_text)

        # 3. Markup contains Undo Delete and Back to Menu
        callbacks = [btn.callback_data for row in call_markup.inline_keyboard for btn in row]
        self.assertIn("undo_action", callbacks)
        self.assertIn("nav:home", callbacks)

        # 4. Verify transaction is deleted in DB
        self.assertIsNone(get_transaction_by_id(tx_id, workspace_id=get_default_workspace_id()))

    def test_delete_confirm_when_already_deleted(self):
        """Verify tapping Confirm Delete on an already deleted record still shows Delete Confirmed clearly."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        from database.queries import delete_transaction
        from config import TELEGRAM_USER_ID

        tx = Transaction(
            amount=50.0,
            transaction_type="SENT",
            person_name="Ramesh",
            transaction_date="2026-09-23",
            category="Food"
        )
        tx_id = insert_transaction_with_balance(tx)
        delete_transaction(tx_id, workspace_id=get_default_workspace_id()) # Already deleted

        update = MagicMock()
        query = MagicMock()
        query.data = f"delete_confirm:{tx_id}"
        owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else 12345
        query.from_user.id = owner_id
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"
        query.message.text = "Delete Transaction"
        query.edit_message_text = AsyncMock()
        query.answer = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        import asyncio
        with patch('config.TELEGRAM_USER_ID', owner_id):
            asyncio.run(handle_callback_query(update, context))

        query.answer.assert_called_with("✅ Delete Confirmed!", show_alert=False)
        query.edit_message_text.assert_called_once()
        call_text = query.edit_message_text.call_args[0][0]
        self.assertIn("Delete Confirmed!", call_text)
        self.assertIn(f"Transaction #{tx_id} is already deleted", call_text)

    def test_delete_cancel_flow(self):
        """Verify tapping Cancel on delete prompt dismisses spinner and edits message cleanly."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        from config import TELEGRAM_USER_ID

        update = MagicMock()
        query = MagicMock()
        query.data = "delete_cancel:999"
        owner_id = int(TELEGRAM_USER_ID) if TELEGRAM_USER_ID else 12345
        query.from_user.id = owner_id
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"
        query.message.text = "Delete Transaction"
        query.edit_message_text = AsyncMock()
        query.answer = AsyncMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {'action': 'waiting_delete_id'}

        import asyncio
        with patch('config.TELEGRAM_USER_ID', owner_id):
            asyncio.run(handle_callback_query(update, context))

        query.answer.assert_called_with("❌ Deletion cancelled.", show_alert=False)
        query.edit_message_text.assert_called_once()
        call_text = query.edit_message_text.call_args[0][0]
        self.assertIn("Deletion Cancelled", call_text)
        self.assertNotIn('action', context.user_data)

    def test_backup_command(self):
        """Verify /backup displays status and Backup Now / Restore options."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.commands import backup_command
        import asyncio

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        with patch('bot.commands.require_admin', AsyncMock(return_value=True)):
            asyncio.run(backup_command(update, context))

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        markup = update.message.reply_text.call_args[1]['reply_markup']
        self.assertIn("Backup & Disaster Recovery Status", reply_text)
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("backup_now", callbacks)
        self.assertIn("nav:restore_info", callbacks)

    def test_undo_command_prompt_with_details(self):
        """Verify /undo asks confirmation with transaction details when action is pending."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.commands import undo_command
        import asyncio

        update = MagicMock()
        update.effective_chat.id = 123
        update.effective_user.id = 456
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        dummy_action = {'action': 'delete', 'uid': 'test-uid-123'}
        dummy_tx = {'id': 99, 'person_name': 'Ramesh', 'amount': 150.0, 'transaction_date': '2026-09-23', 'transaction_type': 'SENT'}

        with patch('bot.commands.require_admin', AsyncMock(return_value=True)), \
             patch('services.undo_service.get_last_action', return_value=dummy_action), \
             patch('database.db.get_db_connection') as mock_conn:
            cursor = MagicMock()
            cursor.fetchone.return_value = dummy_tx
            mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
            asyncio.run(undo_command(update, context))

        update.message.reply_text.assert_called_once()
        prompt_text = update.message.reply_text.call_args[0][0]
        markup = update.message.reply_text.call_args[1]['reply_markup']
        self.assertIn("Undo Delete Transaction?", prompt_text)
        self.assertIn("Ramesh", prompt_text)
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("undo_confirm", callbacks)
        self.assertIn("undo_cancel", callbacks)

    def test_edit_tx_permission_gate(self):
        """Verify edit_tx checks can_user_modify_transaction before rendering edit UI."""
        from bot.handlers import handle_callback_query
        from unittest.mock import MagicMock, AsyncMock, patch
        import asyncio

        update = MagicMock()
        query = AsyncMock()
        query.data = "edit_tx:42"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        update.effective_user.id = 99999
        context = MagicMock()

        dummy_tx = {'id': 42, 'person_name': 'Test', 'amount': 100.0, 'telegram_user_id': 11111}

        # Case 1: Unauthorized user -> denied with alert
        with patch('bot.handlers.get_transaction_by_id', return_value=dummy_tx), \
             patch('database.queries.can_user_modify_transaction', return_value=False), \
             patch('bot.handlers.is_owner', return_value=False), \
             patch('bot.handlers.is_admin_user', return_value=False), \
             patch('bot.handlers.require_member', AsyncMock(return_value=True)):
            asyncio.run(handle_callback_query(update, context))
            query.answer.assert_any_call("⛔ You do not have permission to edit this transaction.", show_alert=True)
            query.edit_message_text.assert_not_called()

        # Case 2: Authorized user -> renders edit UI
        query.reset_mock()
        with patch('bot.handlers.get_transaction_by_id', return_value=dummy_tx), \
             patch('database.queries.can_user_modify_transaction', return_value=True), \
             patch('bot.handlers.is_owner', return_value=True):
            asyncio.run(handle_callback_query(update, context))
            query.edit_message_text.assert_called_once()
            self.assertIn("Edit Transaction #42", query.edit_message_text.call_args[0][0])

    def test_undo_confirm_callback(self):
        """Verify tapping Confirm Undo executes perform_undo and updates card."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        import asyncio

        update = MagicMock()
        query = MagicMock()
        query.data = "undo_confirm"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        owner_id = 12345
        update.effective_user.id = owner_id
        update.effective_chat.id = owner_id
        update.effective_chat.type = "private"
        query.from_user.id = owner_id

        with patch('config.TELEGRAM_USER_ID', owner_id), \
             patch('services.undo_service.perform_undo', return_value=(True, "Restored transaction #99")), \
             patch('database.queries.get_balance_setting', return_value=5000.0), \
             patch('services.backup_service.backup_to_telegram', AsyncMock(return_value=True)) as mock_bkp:
            asyncio.run(handle_callback_query(update, context))

        mock_bkp.assert_not_called()
        query.answer.assert_called_with("↩️ Processing Undo...", show_alert=False)
        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]
        self.assertIn("Undo Confirmed & Applied!", text)
        self.assertIn("Restored transaction #99", text)

    def test_delete_tx_detail_preview(self):
        """Verify tapping Delete on transaction detail card formats full preview."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        import asyncio

        update = MagicMock()
        query = MagicMock()
        query.data = "delete_tx:42"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        dummy_tx = {'id': 42, 'person_name': 'Suresh', 'amount': 250.0, 'transaction_date': '2026-09-23', 'transaction_type': 'SENT'}

        with patch('bot.handlers.is_admin_user', return_value=True), \
             patch('bot.handlers.require_admin', AsyncMock(return_value=True)), \
             patch('bot.handlers.get_transaction_by_id', return_value=dummy_tx):
            asyncio.run(handle_callback_query(update, context))

        query.edit_message_text.assert_called_once()
        prompt_text = query.edit_message_text.call_args[0][0]
        markup = query.edit_message_text.call_args[1]['reply_markup']
        self.assertIn("Delete Transaction #42?", prompt_text)
        self.assertIn("Suresh", prompt_text)
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("delete_confirm:42", callbacks)
        self.assertIn("delete_cancel:42", callbacks)

    def test_handle_text_backup_undo_and_delete_routing(self):
        """Verify handle_text properly routes '\\backup', 'undo', 'delete 42' and 'edit 42'."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_text
        import asyncio

        def make_update(text):
            up = MagicMock()
            up.message.text = text
            up.message.chat_id = 123
            up.message.message_id = 456
            return up

        context = MagicMock()

        with patch('bot.handlers.require_authorized', AsyncMock(return_value=True)), \
             patch('bot.commands.backup_command', AsyncMock()) as mock_backup, \
             patch('bot.commands.undo_command', AsyncMock()) as mock_undo, \
             patch('bot.commands.delete_command', AsyncMock()) as mock_delete, \
             patch('bot.commands.edit_command', AsyncMock()) as mock_edit:

            # Test backup variations
            asyncio.run(handle_text(make_update(r"\backup"), context))
            mock_backup.assert_called_once()

            # Test undo variations
            asyncio.run(handle_text(make_update("undo"), context))
            mock_undo.assert_called_once()

            # Test delete with args
            asyncio.run(handle_text(make_update("delete 42"), context))
            mock_delete.assert_called_once()
            self.assertEqual(context.args, ['42'])

            # Test edit with args
            asyncio.run(handle_text(make_update("/edit 42"), context))
            mock_edit.assert_called_once()
            self.assertEqual(context.args, ['42'])

        with patch('bot.handlers.require_authorized', AsyncMock(return_value=True)), \
             patch('bot.commands.export_command', AsyncMock()) as mock_export:

            # 'export csv' triggers export
            asyncio.run(handle_text(make_update("export csv"), context))
            mock_export.assert_called_once()
            mock_export.reset_mock()

            # 'report pdf' triggers export
            asyncio.run(handle_text(make_update("report pdf"), context))
            mock_export.assert_called_once()
            mock_export.reset_mock()

            # 'exported' and 'reporting' do NOT trigger export
            asyncio.run(handle_text(make_update("exported data yesterday"), context))
            mock_export.assert_not_called()

            asyncio.run(handle_text(make_update("reporting this issue"), context))
            mock_export.assert_not_called()


    def test_save_p_on_image_receipt_shows_saved_card_and_real_balance(self):
        """Verify saving an image receipt properly updates the message with 'Payment Saved!', real balance, and undo entry."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query, set_pending_transaction
        from database.queries import update_balance_setting, get_balance_setting
        from services.undo_service import get_last_action
        import uuid
        import asyncio

        orig_bal = get_balance_setting()
        try:
            update_balance_setting(50000.0)

            ref_no = f"IMG_REF_{uuid.uuid4().hex[:6]}"
            tx_receipt = Transaction(
                amount=250.0,
                transaction_type="SENT",
                person_name="Chai Point",
                category="Food & Dining",
                transaction_date="2026-09-23",
                transaction_time="11:30 AM",
                reference_number=ref_no
            )
            pid = f"pid_{uuid.uuid4().hex[:6]}"
            set_pending_transaction(pid, tx_receipt)

            update = MagicMock()
            query = MagicMock()
            query.data = f"save_p:{pid}"
            owner_id = 998877
            query.from_user.id = owner_id
            update.effective_user.id = owner_id
            update.effective_chat.id = owner_id
            query.message = MagicMock()
            query.message.chat_id = owner_id
            query.message.text = "Detected Receipt"
            query.message.caption = None
            query.message.photo = None
            query.edit_message_text = AsyncMock()
            query.answer = AsyncMock()
            update.callback_query = query
            context = MagicMock()

            update.effective_chat.type = "private"
            with patch('config.TELEGRAM_USER_ID', owner_id), \
                 patch('services.task_manager.schedule_debounced_backup'):
                asyncio.run(handle_callback_query(update, context))

            query.edit_message_text.assert_called_once()
            saved_text = query.edit_message_text.call_args[0][0]
            self.assertIn("Payment Saved!", saved_text)
            self.assertIn("Chai Point", saved_text)
            self.assertIn("₹250", saved_text)
            # Verify balance is not missing or hardcoded, but matches real recalculated balance
            from database.queries import get_transaction_by_reference
            saved_db_tx = get_transaction_by_reference(ref_no)
            self.assertIsNotNone(saved_db_tx)
            self.assertIn("Current Balance:", saved_text)
            self.assertIn(format_currency(saved_db_tx['balance_after']), saved_text)

            # Verify undo record exists
            undo_act = get_last_action(chat_id=owner_id, user_id=owner_id)
            self.assertIsNotNone(undo_act)
            self.assertEqual(undo_act.get('action'), 'insert')
            self.assertIsNotNone(undo_act.get('uid'))
        finally:
            update_balance_setting(orig_bal)

    def test_safe_edit_callback_message_caption_and_fallback(self):
        """Verify safe_edit_callback_message routes to edit_message_caption for media and falls back to plain text."""
        from unittest.mock import MagicMock, AsyncMock
        from bot.handlers import safe_edit_callback_message
        import asyncio

        # Case 1: Media message with caption
        query_media = MagicMock()
        query_media.message.text = None
        query_media.message.caption = "Original Caption"
        query_media.edit_message_caption = AsyncMock()
        query_media.edit_message_text = AsyncMock()

        asyncio.run(safe_edit_callback_message(query_media, "<b>Saved!</b>"))
        query_media.edit_message_caption.assert_called_once()
        self.assertEqual(query_media.edit_message_caption.call_args[1]['caption'], "<b>Saved!</b>")
        query_media.edit_message_text.assert_not_called()

        # Case 2: Parse error on text edit retries with stripped plain text
        query_text = MagicMock()
        query_text.message.text = "Some text"
        query_text.message.caption = None
        # First call fails (simulating Bad Request: Can't parse entities), second call succeeds
        query_text.edit_message_text = AsyncMock(side_effect=[Exception("Can't parse entities"), None])
        asyncio.run(safe_edit_callback_message(query_text, "<b>Broken Tag"))
        self.assertEqual(query_text.edit_message_text.call_count, 2)
        # The retry call should have stripped HTML tags
        self.assertEqual(query_text.edit_message_text.call_args_list[1][0][0], "Broken Tag")

    def test_callback_validation_spec_guard(self):
        """Verify that callbacks with missing or invalid arguments are rejected with 'Invalid button data.'"""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        import asyncio

        malformed_samples = [
            "undo_tx:not_an_int",
            "undo_tx",
            "delete_confirm:abc",
            "close_month:2026",
            "close_month:abc:def",
            "rec_paid:not_int",
            "export_file",
            "quick_add:SENT:not_a_float:Person",
            "auth_grant:not_int:member",
        ]

        import config
        from telegram import Update, User, Chat, CallbackQuery

        owner_id = getattr(config, "TELEGRAM_USER_ID", 11111111)

        for data in malformed_samples:
            update = MagicMock(spec=Update)
            user = MagicMock(spec=User)
            user.id = owner_id
            update.effective_user = user
            chat = MagicMock(spec=Chat)
            chat.id = owner_id
            chat.type = 'private'
            update.effective_chat = chat

            query = MagicMock(spec=CallbackQuery)
            query.data = data
            query.from_user = user
            query.answer = AsyncMock()
            update.callback_query = query
            context = MagicMock()

            with patch("bot.handlers.is_owner", return_value=True), \
                 patch("bot.handlers.require_owner", AsyncMock(return_value=True)), \
                 patch("bot.handlers.require_admin", AsyncMock(return_value=True)), \
                 patch("bot.handlers.require_member", AsyncMock(return_value=True)), \
                 patch("bot.handlers.require_authorized", AsyncMock(return_value=True)):
                asyncio.run(handle_callback_query(update, context))
            query.answer.assert_called_with("❌ Invalid button data.", show_alert=True)

    def test_pending_transactions_ttl_pruning(self):
        """Verify that pending_transactions prunes expired entries on set, fetch, and pop."""
        import time
        from bot.handlers import (
            pending_transactions, _pending_transactions_timestamps,
            set_pending_transaction, fetch_pending_transaction, pop_pending_transaction
        )
        from database.models import Transaction

        t1 = Transaction(amount=100.0, person_name="Alice", transaction_type="SENT")
        t2 = Transaction(amount=200.0, person_name="Bob", transaction_type="SENT")

        set_pending_transaction("fresh_1", t1)
        set_pending_transaction("stale_1", t2)

        # Manually backdate stale_1 to 25 hours ago
        _pending_transactions_timestamps["stale_1"] = time.time() - 90000

        # fetch triggers pruning
        fetch_pending_transaction("fresh_1")
        assert "stale_1" not in pending_transactions
        assert "stale_1" not in _pending_transactions_timestamps
        assert "fresh_1" in pending_transactions

    def test_monthly_closing_keyboard_clamping_and_role_filtering(self):
        """Verify get_monthly_closing_keyboard clamps y/m and hides privileged buttons from viewers."""
        from bot.keyboards import get_monthly_closing_keyboard

        # Viewer role: no close_month or export buttons
        kb_viewer = get_monthly_closing_keyboard(year=1999, month=15, is_closed=False, role="viewer")
        viewer_callbacks = [btn.callback_data for row in kb_viewer.inline_keyboard for btn in row]
        self.assertFalse(any(cb.startswith("close_month:") for cb in viewer_callbacks))
        self.assertFalse(any(cb.startswith("export_file:") for cb in viewer_callbacks))
        # Navigation clamped to 2000 and 12
        self.assertTrue(any("nav:month_close:2000:11" in cb for cb in viewer_callbacks))

        # Admin role: has both review and export buttons
        kb_admin = get_monthly_closing_keyboard(year=2026, month=9, is_closed=False, role="admin")
        admin_callbacks = [btn.callback_data for row in kb_admin.inline_keyboard for btn in row]
        self.assertTrue(any(cb.startswith("close_month:2026:9") for cb in admin_callbacks))
        self.assertTrue(any(cb == "export_file:excel" for cb in admin_callbacks))

        # Member role: has review button but NOT export button
        kb_member = get_monthly_closing_keyboard(year=2026, month=9, is_closed=False, role="member")
        member_callbacks = [btn.callback_data for row in kb_member.inline_keyboard for btn in row]
        self.assertTrue(any(cb.startswith("close_month:2026:9") for cb in member_callbacks))
        self.assertFalse(any(cb == "export_file:excel" for cb in member_callbacks))

    def test_close_month_range_validation(self):
        """Verify close_month callback rejects out-of-range year/month."""
        from bot.handlers import handle_callback_query
        from unittest.mock import MagicMock, AsyncMock, patch
        import asyncio

        update = MagicMock()
        query = AsyncMock()
        query.data = "close_month:1800:1"
        query.answer = AsyncMock()
        update.callback_query = query
        context = MagicMock()

        with patch("bot.handlers.is_owner", return_value=True), \
             patch("bot.handlers.require_owner", AsyncMock(return_value=True)), \
             patch("bot.handlers.require_admin", AsyncMock(return_value=True)):
            asyncio.run(handle_callback_query(update, context))
            query.answer.assert_called_with("❌ Invalid month or year range.", show_alert=True)

    def test_callback_auth_denial_paths_for_viewer_and_unauthorized_user(self):
        """Verify that handle_callback_query enforces real auth and denies viewers and unauthorized users on mutating callbacks."""
        from unittest.mock import MagicMock, AsyncMock
        from database.queries import add_workspace_member, get_default_workspace_id
        from bot.handlers import handle_callback_query, set_pending_transaction, fetch_pending_transaction
        import asyncio

        ws_id = get_default_workspace_id()
        viewer_id = 7770001
        stranger_id = 8880002

        # Provision viewer_id as a strictly 'viewer' role in the workspace
        add_workspace_member(ws_id, viewer_id, role="viewer")

        # Create a transaction to attempt deleting
        tx = Transaction(
            amount=50.0,
            transaction_type="SENT",
            person_name="Denial Test Payee",
            transaction_date="2026-09-25",
            category="General"
        )
        tx_id = insert_transaction_with_balance(tx)

        # 1. Viewer attempts delete_confirm -> denied, tx remains in DB
        up_viewer = MagicMock()
        q_viewer = MagicMock()
        q_viewer.data = f"delete_confirm:{tx_id}"
        q_viewer.from_user.id = viewer_id
        up_viewer.effective_user.id = viewer_id
        up_viewer.effective_chat.id = viewer_id
        up_viewer.effective_chat.type = "private"
        q_viewer.answer = AsyncMock()
        q_viewer.edit_message_text = AsyncMock()
        up_viewer.callback_query = q_viewer
        context = MagicMock()

        asyncio.run(handle_callback_query(up_viewer, context))
        remaining_tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        self.assertIsNotNone(remaining_tx, "Viewer was incorrectly permitted to delete transaction!")

        # 2. Unauthorized stranger attempts delete_confirm -> denied, tx remains in DB
        up_stranger = MagicMock()
        q_stranger = MagicMock()
        q_stranger.data = f"delete_confirm:{tx_id}"
        q_stranger.from_user.id = stranger_id
        up_stranger.effective_user.id = stranger_id
        up_stranger.effective_chat.id = stranger_id
        up_stranger.effective_chat.type = "private"
        q_stranger.answer = AsyncMock()
        q_stranger.edit_message_text = AsyncMock()
        up_stranger.callback_query = q_stranger

        asyncio.run(handle_callback_query(up_stranger, context))
        remaining_tx2 = get_transaction_by_id(tx_id, workspace_id=ws_id)
        self.assertIsNotNone(remaining_tx2, "Stranger was incorrectly permitted to delete transaction!")

        # 3. Viewer attempts save_p -> denied
        pid_v = "pid_viewer_test"
        set_pending_transaction(pid_v, tx, workspace_id=ws_id)
        q_viewer.data = f"save_p:{pid_v}"
        q_viewer.reset_mock()
        asyncio.run(handle_callback_query(up_viewer, context))
        # Pending transaction must still exist or not be committed by viewer
        pending_after = fetch_pending_transaction(pid_v, workspace_id=ws_id)
        self.assertIsNotNone(pending_after, "Viewer was incorrectly permitted to consume pending receipt!")

        # 4. Stranger attempts save_p -> denied
        pid_s = "pid_stranger_test"
        set_pending_transaction(pid_s, tx, workspace_id=ws_id)
        q_stranger.data = f"save_p:{pid_s}"
        q_stranger.reset_mock()
        asyncio.run(handle_callback_query(up_stranger, context))
        pending_after_s = fetch_pending_transaction(pid_s, workspace_id=ws_id)
        self.assertIsNotNone(pending_after_s, "Stranger was incorrectly permitted to consume pending receipt!")

        # 5. Viewer attempts undo_confirm -> denied
        q_viewer.data = "undo_confirm"
        q_viewer.reset_mock()
        asyncio.run(handle_callback_query(up_viewer, context))
        if q_viewer.edit_message_text.called:
            text = q_viewer.edit_message_text.call_args[0][0]
            self.assertNotIn("Undo Confirmed & Applied!", text)

        # 6. Stranger attempts undo_confirm -> denied
        q_stranger.data = "undo_confirm"
    def test_messageless_callbacks_do_not_crash(self):
        """Verify export_file, ws_reset, cafe_stats, cafe_view_menu gracefully handle query.message is None without raising AttributeError."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from bot.handlers import handle_callback_query
        import asyncio

        context = MagicMock()
        context.user_data = {}

        for action_data in ["export_file:pdf", "export_file:excel", "ws_reset", "ws_reset_menu", "cafe_stats", "cafe_view_menu"]:
            query = MagicMock()
            query.data = action_data
            query.message = None
            query.from_user.id = 123456
            query.answer = AsyncMock()

            update = MagicMock()
            update.callback_query = query
            update.effective_user.id = 123456
            update.effective_chat.id = 123456

            with patch('bot.handlers.require_authorized', AsyncMock(return_value=True)), \
                 patch('bot.handlers.is_owner', return_value=True), \
                 patch('bot.handlers.require_owner', AsyncMock(return_value=True)), \
                 patch('bot.handlers.require_admin', AsyncMock(return_value=True)):
                # Must not raise AttributeError
                try:
                    asyncio.run(handle_callback_query(update, context))
                except AttributeError as e:
                    self.fail(f"Callback {action_data} crashed with AttributeError when message was None: {e}")



    def test_callback_badrequest_logs_query_data_at_warning_level(self):
        """Verify handle_callback_query catches BadRequest and logs query.data at WARNING level."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from telegram.error import BadRequest
        from bot.handlers import handle_callback_query
        import asyncio

        context = MagicMock()
        query = MagicMock()
        query.data = "nav_home"
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        update.effective_user.id = 123456
        update.effective_chat.id = 123456

        with patch('bot.handlers._dispatch_callback_query', AsyncMock(side_effect=BadRequest("Message is not modified"))), \
             patch('bot.handlers.logger.warning') as mock_log_warn:
            asyncio.run(handle_callback_query(update, context))
            mock_log_warn.assert_called()
            # Assert query.data is in the logged message
            log_args = mock_log_warn.call_args[0]
            log_formatted = log_args[0] % log_args[1:] if len(log_args) > 1 else log_args[0]
            self.assertIn("nav_home", log_formatted)
            self.assertIn("BadRequest", log_formatted)

if __name__ == "__main__":
    unittest.main()





