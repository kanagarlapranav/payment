import unittest
import html
from database.db import get_db_connection
from database.models import Transaction
from database.queries import (
    insert_transaction_with_balance, get_transaction_by_id, get_transactions_paginated,
    get_payee_category, remember_payee_category
)
from bot.keyboards import (
    get_home_menu_keyboard, get_add_menu_keyboard, get_more_menu_keyboard,
    get_transaction_detail_keyboard, get_backup_status_keyboard,
    get_history_paginated_keyboard, get_quick_add_keyboard,
    get_receipt_confirm_keyboard, get_receipt_edit_fields_keyboard,
    get_help_keyboard, get_balance_keyboard, get_stats_keyboard,
    get_budget_keyboard, get_insights_keyboard, get_digest_keyboard,
    get_cafestats_keyboard, get_standard_nav_keyboard
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
            get_receipt_confirm_keyboard(pending_id="abc123def4", duplicate_warning=True),
            get_receipt_edit_fields_keyboard(pending_id="abc123def4"),
            get_help_keyboard(),
            get_balance_keyboard(),
            get_stats_keyboard(),
            get_budget_keyboard(),
            get_insights_keyboard(),
            get_digest_keyboard(),
            get_cafestats_keyboard(),
            get_standard_nav_keyboard()
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
        kb_confirm = get_receipt_confirm_keyboard(pending_id, duplicate_warning=True)
        callbacks_confirm = [btn.callback_data for row in kb_confirm.inline_keyboard for btn in row]
        
        self.assertIn(f"save_p:{pending_id}", callbacks_confirm)
        self.assertIn(f"edit_p:{pending_id}", callbacks_confirm)
        self.assertIn(f"cat_p:{pending_id}", callbacks_confirm)
        self.assertIn(f"cancel_p:{pending_id}", callbacks_confirm)

        kb_edit = get_receipt_edit_fields_keyboard(pending_id)
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

if __name__ == "__main__":
    unittest.main()
