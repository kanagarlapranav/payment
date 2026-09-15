"""
Cafeteria Menu Database & Smart Item Recommender.
Contains vegetarian menu items, prices, add-on charges (packing, extra spicy),
and automatic matching algorithms for payments made to Vikraman Nair / Cafeteria.
"""
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

CAFETERIA_MERCHANT_NAMES = [
    "vikraman nair", "vikram nair", "vikramannair066@fbl",
    "vikraman nair k", "cafeteria", "canteen"
]

@dataclass
class MenuItem:
    name: str
    price: float
    category: str # Breakfast, Lunch, Veg Dishes, Snacks & Tea, Juices, Ice Cream
    is_veg: bool = True

# Complete Vegetarian Menu parsed from Cafeteria Board
VEG_MENU: List[MenuItem] = [
    # Breakfast
    MenuItem("Plain Dosa", 10.0, "Breakfast"),
    MenuItem("Idly (1 pc)", 7.0, "Breakfast"),
    MenuItem("Poori (1 pc)", 7.0, "Breakfast"),
    MenuItem("Poori Masala (1 set)", 20.0, "Breakfast"),
    MenuItem("Idiyappam (3 pcs)", 25.0, "Breakfast"),
    MenuItem("Ghee Dosa", 30.0, "Breakfast"),
    MenuItem("Onion Dosa", 30.0, "Breakfast"),
    MenuItem("Masala Dosa", 35.0, "Breakfast"),
    MenuItem("Puttu Kadala", 40.0, "Breakfast"),

    # Lunch & Breads
    MenuItem("Chapathi (1 pc)", 6.0, "Lunch"),
    MenuItem("Parota (1 pc)", 8.0, "Lunch"),
    MenuItem("Aatta Parota (1 pc)", 25.0, "Lunch"),
    MenuItem("Veg Meals", 45.0, "Lunch"),
    MenuItem("Veg Fried Rice", 50.0, "Lunch"),
    MenuItem("Veg Noodles", 50.0, "Lunch"),

    # Veg Dishes & Curries
    MenuItem("Veg Rice", 50.0, "Veg Dishes"),
    MenuItem("Jeera Rice", 55.0, "Veg Dishes"),
    MenuItem("Tomato Rice", 55.0, "Veg Dishes"),
    MenuItem("Tomato Fry", 60.0, "Veg Dishes"),
    MenuItem("Dal Fry", 65.0, "Veg Dishes"),
    MenuItem("Dal Tadka", 70.0, "Veg Dishes"),
    MenuItem("Gobi Rice", 80.0, "Veg Dishes"),
    MenuItem("Gobi Manchurian", 80.0, "Veg Dishes"),
    MenuItem("Chilly Gobi", 80.0, "Veg Dishes"),
    MenuItem("Kadai Paneer", 85.0, "Veg Dishes"),
    MenuItem("Paneer Noodles", 85.0, "Veg Dishes"),
    MenuItem("Paneer Rice", 90.0, "Veg Dishes"),
    MenuItem("Chilly Paneer", 90.0, "Veg Dishes"),
    MenuItem("Paneer Butter Masala", 90.0, "Veg Dishes"),
    MenuItem("Paneer Kothu Parota", 90.0, "Veg Dishes"),

    # Tea, Coffee & Snacks
    MenuItem("Black Tea", 5.0, "Snacks & Tea"),
    MenuItem("Black Coffee", 6.0, "Snacks & Tea"),
    MenuItem("Tea", 8.0, "Snacks & Tea"),
    MenuItem("Lemon Tea", 8.0, "Snacks & Tea"),
    MenuItem("Vada", 8.0, "Snacks & Tea"),
    MenuItem("Dal Vada", 8.0, "Snacks & Tea"),
    MenuItem("Pazhampori", 8.0, "Snacks & Tea"),
    MenuItem("Coffee", 10.0, "Snacks & Tea"),
    MenuItem("Chilly Bajji", 10.0, "Snacks & Tea"),
    MenuItem("Milk (1 cup)", 15.0, "Snacks & Tea"),
    MenuItem("Horlicks", 20.0, "Snacks & Tea"),
    MenuItem("Boost", 20.0, "Snacks & Tea"),

    # Fresh Juices
    MenuItem("Lime Juice", 15.0, "Juices"),
    MenuItem("Orange Juice", 30.0, "Juices"),
    MenuItem("Pineapple Juice", 30.0, "Juices"),
    MenuItem("Watermelon Juice", 30.0, "Juices"),
    MenuItem("Grape Juice", 30.0, "Juices"),
    MenuItem("Papaya Juice", 40.0, "Juices"),
    MenuItem("Mosambi Juice", 40.0, "Juices"),

    # Kwality Wall's Ice Creams
    MenuItem("Vanilla Tub", 99.0, "Ice Cream"),
    MenuItem("Strawberry Tub", 125.0, "Ice Cream"),
    MenuItem("Chocolate Tub", 150.0, "Ice Cream"),
    MenuItem("Butterscotch Tub", 150.0, "Ice Cream"),
    MenuItem("Tutti Frutti Tub", 155.0, "Ice Cream"),
    MenuItem("Alphonso Mango Tub", 155.0, "Ice Cream"),
    MenuItem("Choco Brownie Fudge", 180.0, "Ice Cream"),
    MenuItem("Blackcurrant & Raisins", 185.0, "Ice Cream"),
]

ADD_ONS = {
    "Packing Charge": 5.0,
    "Extra Spicy": 5.0
}

def is_cafeteria_payment(person_name: str = "", upi_id: str = "", ocr_text: str = "") -> bool:
    """Checks if a payment was made to Vikraman Nair / Cafeteria."""
    clean = f"{person_name or ''} {upi_id or ''} {ocr_text or ''}".lower()
    clean_nospace = clean.replace(" ", "").replace("_", "").replace("-", "").replace(".", "")
    targets = ["vikramannair", "vikramnair", "vikramannair066@fbl", "cafeteria", "canteen"]
    return any(t in clean_nospace for t in targets)

def find_exact_items(amount: float) -> List[MenuItem]:
    """Finds all single menu items matching the exact amount."""
    return [item for item in VEG_MENU if abs(item.price - amount) < 0.01]

def find_combinations(amount: float, max_items: int = 2) -> List[str]:
    """Finds valid vegetarian combinations (or items + add-ons) that sum to the exact amount."""
    combos = []
    
    # 1. Item + Add-on (Packing / Extra Spicy)
    for add_on_name, add_on_price in ADD_ONS.items():
        rem = amount - add_on_price
        for item in VEG_MENU:
            if abs(item.price - rem) < 0.01:
                combos.append(f"{item.name} + {add_on_name} (₹{amount:.0f})")

    # 2. Pair of 2 Items
    for i, item1 in enumerate(VEG_MENU):
        rem = amount - item1.price
        if rem <= 0:
            continue
        for item2 in VEG_MENU[i:]:
            if abs(item2.price - rem) < 0.01:
                combos.append(f"{item1.name} + {item2.name} (₹{amount:.0f})")

    return combos[:8] # Limit to top 8 suggestions

def get_menu_by_category() -> Dict[str, List[MenuItem]]:
    """Groups the entire vegetarian menu by category."""
    cats = {}
    for item in VEG_MENU:
        cats.setdefault(item.category, []).append(item)
    return cats

def format_full_menu() -> str:
    """Formats the complete vegetarian cafeteria menu into structured HTML for Telegram."""
    cats = get_menu_by_category()
    
    category_icons = {
        "Breakfast": "🥞",
        "Lunch": "🍛",
        "Veg Dishes": "🥘",
        "Snacks & Tea": "☕",
        "Juices": "🧃",
        "Ice Cream": "🍨"
    }
    
    text = "📋 <b>CAFETERIA VEGETARIAN MENU</b>\n"
    text += "🏪 <b>Merchant:</b> VIKRAMAN NAIR K (<code>vikramannair066@fbl</code>)\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for cat_name, items in cats.items():
        icon = category_icons.get(cat_name, "🍽️")
        text += f"{icon} <b>{cat_name.upper()}</b>\n"
        for item in items:
            text += f"• {item.name} — <b>₹{item.price:.0f}</b>\n"
        text += "\n"
        
    text += "📦 <b>ADD-ONS / EXTRAS:</b>\n"
    text += "• Packing Charge — <b>+₹5</b>\n"
    text += "• Extra Spicy — <b>+₹5</b>\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Tip: When you pay Vikraman Nair, the bot will auto-suggest items matching your exact bill!</i>"
    return text

