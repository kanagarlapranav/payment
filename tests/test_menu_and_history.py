import unittest
from bot.commands import render_home_menu_text, render_history_page
from database.db import get_custom_menu_items, delete_custom_menu_item_by_id
from services.cafeteria_service import add_custom_menu_item, delete_custom_menu_item

class TestMenuAndHistory(unittest.TestCase):
    def test_render_home_menu_text_success(self):
        text = render_home_menu_text()
        self.assertIn("Payment Tracker Dashboard", text)
        self.assertIn("Balance:", text)
        self.assertIn("Today:", text)

    def test_render_history_page_page1_shows_latest_5(self):
        from database.models import Transaction
        from database.queries import insert_transaction_with_balance
        t = Transaction()
        t.amount = 100.0
        t.transaction_type = "SENT"
        t.person_name = "History Test"
        t.transaction_date = "2026-09-22"
        t.transaction_time = "00:00:00"
        insert_transaction_with_balance(t)

        text, markup = render_history_page(page=1, filter_type="ALL", page_size=5)
        self.assertIn("Latest", text)
        self.assertIn("History Test", text)
        self.assertIn("Page 1 of", text)
        self.assertIsNotNone(markup)

    def test_custom_menu_items_crud(self):
        # Add test dish
        dish_name = "Paneer Butter Masala Special"
        succ, msg = add_custom_menu_item(dish_name, 120.0, "Veg Dishes", is_veg=True)
        self.assertTrue(succ)

        # Retrieve items
        items = get_custom_menu_items()
        item_match = next((it for it in items if it['name'] == dish_name), None)
        self.assertIsNotNone(item_match)
        self.assertEqual(item_match['price'], 120.0)

        # Delete by id
        del_succ, del_name = delete_custom_menu_item_by_id(item_match['id'])
        self.assertTrue(del_succ)
        self.assertEqual(del_name, dish_name)

        # Confirm deleted
        items_after = get_custom_menu_items()
        self.assertIsNone(next((it for it in items_after if it['name'] == dish_name), None))

    def test_render_history_page_sorting(self):
        from database.models import Transaction
        from database.queries import insert_transaction_with_balance
        t1 = Transaction(amount=50.0, transaction_type="SENT", person_name="Early Person", transaction_date="2026-09-01")
        t2 = Transaction(amount=75.0, transaction_type="RECEIVED", person_name="Late Person", transaction_date="2026-09-20")
        insert_transaction_with_balance(t1)
        insert_transaction_with_balance(t2)

        # Test ascending sort
        text_asc, markup_asc = render_history_page(page=1, filter_type="ALL", page_size=5, sort_by="date_asc")
        self.assertIn("Oldest First", text_asc)

        # Test ID DESC sort
        text_id_desc, markup_id_desc = render_history_page(page=1, filter_type="ALL", page_size=5, sort_by="id_desc")
        self.assertIn("ID Order", text_id_desc)

if __name__ == "__main__":
    unittest.main()
