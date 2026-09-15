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

def get_all_menu_items() -> List[MenuItem]:
    """Returns the complete menu combining default items and custom user-added items from SQLite."""
    items = list(VEG_MENU)
    try:
        from database.db import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, price, category, is_veg FROM custom_menu_items ORDER BY id ASC")
            for row in cursor.fetchall():
                items.append(MenuItem(
                    name=str(row['name']),
                    price=float(row['price']),
                    category=str(row['category'] or 'Snacks & Tea'),
                    is_veg=bool(row['is_veg'])
                ))
    except Exception:
        pass
    return items

def add_custom_menu_item(name: str, price: float, category: str = "Snacks & Tea", is_veg: bool = True) -> Tuple[bool, str]:
    """Adds a new custom vegetarian item to the cafeteria menu database."""
    name_clean = name.strip().title()
    if not name_clean:
        return False, "Item name cannot be empty."
    if price <= 0:
        return False, "Price must be greater than 0."

    # Strict Pure Vegetarian Policy Check
    non_veg_keywords = ["chicken", "egg", "omelette", "omlet", "fish", "meat", "mutton", "beef", "pork", "prawn", "crab"]
    if any(nvk in name_clean.lower() for nvk in non_veg_keywords) or not is_veg:
        return False, "⚠️ Only vegetarian items are permitted in this cafeteria tracker."

    # Prevent duplicates
    all_items = get_all_menu_items()
    if any(it.name.lower() == name_clean.lower() for it in all_items):
        return False, f"Item '<b>{name_clean}</b>' already exists in the menu."

    try:
        from database.db import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO custom_menu_items (name, price, category, is_veg) VALUES (?, ?, ?, 1)",
                (name_clean, float(price), category)
            )
            conn.commit()
        return True, f"✅ Added '<b>{name_clean}</b>' (₹{price:.0f}) to {category}!"
    except Exception as e:
        return False, f"Database error: {e}"


def delete_custom_menu_item(name: str) -> Tuple[bool, str]:
    """Deletes a custom item from the menu."""
    try:
        from database.db import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM custom_menu_items WHERE lower(name) = lower(?)", (name.strip(),))
            if cursor.rowcount > 0:
                conn.commit()
                return True, f"🗑️ Removed '<b>{name}</b>' from menu."
            else:
                return False, f"Item '<b>{name}</b>' not found in custom items."
    except Exception as e:
        return False, f"Error deleting item: {e}"

def is_cafeteria_payment(person_name: str = "", upi_id: str = "", ocr_text: str = "") -> bool:
    """Checks if a payment was made to Vikraman Nair / Cafeteria."""
    clean = f"{person_name or ''} {upi_id or ''} {ocr_text or ''}".lower()
    clean_nospace = clean.replace(" ", "").replace("_", "").replace("-", "").replace(".", "")
    targets = ["vikramannair", "vikramnair", "vikramannair066@fbl", "cafeteria", "canteen"]
    return any(t in clean_nospace for t in targets)

def find_exact_items(amount: float) -> List[MenuItem]:
    """Finds all single menu items matching the exact amount."""
    menu = get_all_menu_items()
    return [item for item in menu if abs(item.price - amount) < 0.01]

def find_combinations(amount: float, max_items: int = 2) -> List[str]:
    """Finds valid vegetarian combinations (or items + add-ons) that sum to the exact amount."""
    combos = []
    menu = get_all_menu_items()
    
    # 1. Item + Add-on (Packing / Extra Spicy)
    for add_on_name, add_on_price in ADD_ONS.items():
        rem = amount - add_on_price
        for item in menu:
            if abs(item.price - rem) < 0.01:
                combos.append(f"{item.name} + {add_on_name} (₹{amount:.0f})")

    # 2. Pair of 2 Items
    for i, item1 in enumerate(menu):
        rem = amount - item1.price
        if rem <= 0:
            continue
        for item2 in menu[i:]:
            if abs(item2.price - rem) < 0.01:
                combos.append(f"{item1.name} + {item2.name} (₹{amount:.0f})")

    return combos[:8] # Limit to top 8 suggestions

def get_menu_by_category() -> Dict[str, List[MenuItem]]:
    """Groups the entire vegetarian menu by category."""
    cats = {}
    for item in get_all_menu_items():
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

def format_cafeteria_stats() -> str:
    """Computes and formats cafeteria spending insights and top ordered veg items."""
    import html
    from database.queries import get_cafeteria_transactions
    from utils.currency import format_currency
    from collections import Counter
    import datetime

    txs = get_cafeteria_transactions(limit=200)
    if not txs:
        return (
            "🍽️ <b>CAFETERIA SPENDING INSIGHTS</b>\n"
            "🏪 <b>Merchant:</b> VIKRAMAN NAIR K\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>No cafeteria payments recorded yet. Scan a receipt or log a payment to see insights!</i>"
        )

    now = datetime.datetime.now()
    curr_month_str = now.strftime("%Y-%m")

    month_txs = [t for t in txs if str(t.get('transaction_date', '')).startswith(curr_month_str)]
    month_total = sum(float(t.get('amount') or 0.0) for t in month_txs)
    all_time_total = sum(float(t.get('amount') or 0.0) for t in txs)
    
    # Extract item tags
    item_counts = Counter()
    for t in txs:
        p_name = str(t.get('person_name') or "")
        if "Cafeteria:" in p_name:
            tag = p_name.split("Cafeteria:", 1)[1].rstrip(')').strip()
            item_counts[tag] += 1

    month_name = now.strftime("%B %Y")
    avg_bill = month_total / len(month_txs) if month_txs else (all_time_total / len(txs) if txs else 0.0)

    text = "🍽️ <b>CAFETERIA SPENDING INSIGHTS</b>\n"
    text += "🏪 <b>Merchant:</b> VIKRAMAN NAIR K (<code>vikramannair066@fbl</code>)\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📅 <b>{month_name} Spends:</b> <b>{html.escape(format_currency(month_total))}</b> ({len(month_txs)} visits)\n"
    text += f"💰 <b>All-Time Cafeteria Total:</b> <b>{html.escape(format_currency(all_time_total))}</b> ({len(txs)} orders)\n"
    text += f"📊 <b>Average Spend / Visit:</b> <b>{html.escape(format_currency(avg_bill))}</b>\n\n"

    if item_counts:
        text += "🏆 <b>MOST ORDERED VEG ITEMS:</b>\n"
        for item, count in item_counts.most_common(5):
            times_str = f"{count} times" if count > 1 else "1 time"
            text += f"• 🍽️ <b>{html.escape(item)}</b> — {times_str}\n"
        text += "\n"

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Tip: Use <code>/menu</code> to view the full vegetarian menu or <code>/cafeedit</code> to edit tagged items.</i>"
    return text


