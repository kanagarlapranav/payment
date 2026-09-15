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

if __name__ == '__main__':
    unittest.main()
