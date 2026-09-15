import unittest
from services.cafeteria_service import (
    is_cafeteria_payment, find_exact_items, find_combinations,
    format_full_menu, VEG_MENU, ADD_ONS
)

class TestCafeteriaService(unittest.TestCase):
    def test_cafeteria_recognition(self):
        self.assertTrue(is_cafeteria_payment("VIKRAMAN NAIR K"))
        self.assertTrue(is_cafeteria_payment("Vikram Nair"))
        self.assertTrue(is_cafeteria_payment("Random Person", upi_id="vikramannair066@fbl"))
        self.assertTrue(is_cafeteria_payment("College Cafeteria"))
        self.assertFalse(is_cafeteria_payment("Balaji Admin"))

    def test_all_items_are_vegetarian(self):
        non_veg_keywords = ["chicken", "egg", "fish", "meat", "mutton", "beef", "pork"]
        for item in VEG_MENU:
            item_lower = item.name.lower()
            for nvk in non_veg_keywords:
                self.assertNotIn(nvk, item_lower, f"Found non-veg item in database: {item.name}")

    def test_find_exact_items_60(self):
        items = find_exact_items(60.0)
        item_names = [i.name for i in items]
        self.assertIn("Tomato Fry", item_names)

    def test_find_exact_items_50(self):
        items = find_exact_items(50.0)
        item_names = [i.name for i in items]
        self.assertIn("Veg Fried Rice", item_names)
        self.assertIn("Veg Noodles", item_names)
        self.assertIn("Veg Rice", item_names)

    def test_find_combinations_60(self):
        combos = find_combinations(60.0)
        # Check that we get valid combinations (e.g. Jeera Rice + Packing or items summing to 60)
        self.assertTrue(len(combos) > 0)
        # e.g. "Jeera Rice + Packing Charge (₹60)" or "Tomato Rice + Packing Charge (₹60)"
        self.assertTrue(any("Packing" in c or "Spicy" in c or "+" in c for c in combos))

    def test_format_full_menu(self):
        text = format_full_menu()
        self.assertIn("CAFETERIA VEGETARIAN MENU", text)
        self.assertIn("VIKRAMAN NAIR K", text)
        self.assertIn("Packing Charge", text)
        self.assertIn("Extra Spicy", text)
        self.assertIn("Plain Dosa", text)
        self.assertIn("Chilly Bajji", text)

    def test_format_cafeteria_stats(self):
        from services.cafeteria_service import format_cafeteria_stats
        stats = format_cafeteria_stats()
        self.assertIn("CAFETERIA SPENDING INSIGHTS", stats)
        self.assertIn("VIKRAMAN NAIR K", stats)

    def test_cafeteria_keyboards(self):
        from bot.keyboards import (
            get_cafeteria_selection_keyboard, get_cafeteria_single_item_keyboard,
            get_cafeteria_two_items_keyboard, get_cafeteria_cart_keyboard,
            get_cafeteria_tagged_keyboard
        )
        # Initial selection
        kb1 = get_cafeteria_selection_keyboard(1, 20.0)
        self.assertTrue(len(kb1.inline_keyboard) > 0)
        
        # Single item
        kb_single = get_cafeteria_single_item_keyboard(1, 20.0)
        self.assertTrue(len(kb_single.inline_keyboard) > 0)
        
        # Two items
        kb_two = get_cafeteria_two_items_keyboard(1, 20.0)
        self.assertTrue(len(kb_two.inline_keyboard) > 0)
        
        # Cart / Plate builder
        kb_cart = get_cafeteria_cart_keyboard(1, 20.0, [{'name': 'Plain Dosa', 'price': 10.0}])
        self.assertTrue(len(kb_cart.inline_keyboard) > 0)
        
    def test_custom_menu_item_flow(self):
        from services.cafeteria_service import add_custom_menu_item, delete_custom_menu_item, get_all_menu_items
        # Non-veg rejection
        succ, msg = add_custom_menu_item("Chicken Biryani", 100, "Rice")
        self.assertFalse(succ)
        self.assertIn("vegetarian", msg.lower())

        # Valid veg addition
        succ, msg = add_custom_menu_item("Special Paneer Roll", 45, "Snacks", is_veg=True)
        self.assertTrue(succ)

        all_items = get_all_menu_items()
        self.assertTrue(any(i.name == "Special Paneer Roll" for i in all_items))

        # Delete custom item
        succ_del, msg_del = delete_custom_menu_item("Special Paneer Roll")
        self.assertTrue(succ_del)

        all_items_after = get_all_menu_items()
        self.assertFalse(any(i.name == "Special Paneer Roll" for i in all_items_after))

if __name__ == '__main__':
    unittest.main()


