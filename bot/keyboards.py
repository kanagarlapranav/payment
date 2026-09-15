from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_confirmation_keyboard(tx_id: str = ""):
    """Returns inline keyboard for confirming uncertain transactions."""
    confirm_cb = f"confirm_tx:{tx_id}" if tx_id else "confirm_tx"
    cancel_cb = f"cancel_tx:{tx_id}" if tx_id else "cancel_tx"
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data=confirm_cb),
            InlineKeyboardButton("❌ Cancel", callback_data=cancel_cb)
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_edit_fields_keyboard(tx_id: int):
    """Returns inline keyboard of fields available to edit for a transaction."""
    keyboard = [
        [
            InlineKeyboardButton("💵 Amount", callback_data=f"edit_field:{tx_id}:amount"),
            InlineKeyboardButton("👤 Person Name", callback_data=f"edit_field:{tx_id}:person")
        ],
        [
            InlineKeyboardButton("📅 Date", callback_data=f"edit_field:{tx_id}:date"),
            InlineKeyboardButton("🔄 Type (SENT/RECV)", callback_data=f"edit_field:{tx_id}:type")
        ],
        [
            InlineKeyboardButton("🔢 Ref / UTR", callback_data=f"edit_field:{tx_id}:ref"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"edit_cancel:{tx_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_delete_confirm_keyboard(tx_id: int):
    """Returns inline keyboard to confirm deleting a transaction."""
    keyboard = [
        [
            InlineKeyboardButton("🗑️ Confirm Delete", callback_data=f"delete_confirm:{tx_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"delete_cancel:{tx_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_transaction_selection_keyboard(transactions: list, action_prefix: str):
    """Returns a list of buttons for selecting a transaction."""
    from utils.currency import format_currency
    keyboard = []
    for t in transactions[:6]: # Show top 6
        person = t.get('person_name') or t.get('transaction_type', '')
        badge = "🟢" if t.get('transaction_type') == 'RECEIVED' else "🔴"
        amt = format_currency(t.get('amount', 0))
        btn_label = f"#{t['id']} {badge} {person[:12]} - {amt}"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"{action_prefix}:{t['id']}")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"{action_prefix}_cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_filter_keyboard():
    """Returns interactive filter options keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📅 Today", callback_data="filter:today"),
            InlineKeyboardButton("📅 Yesterday", callback_data="filter:yesterday")
        ],
        [
            InlineKeyboardButton("🗓️ This Month", callback_data="filter:this_month"),
            InlineKeyboardButton("📊 Monthly Stats", callback_data="filter:monthly_stats")
        ],
        [
            InlineKeyboardButton("🔴 Sent Only", callback_data="filter:type_sent"),
            InlineKeyboardButton("🟢 Received Only", callback_data="filter:type_received")
        ],
        [
            InlineKeyboardButton("🆔 Show With IDs", callback_data="filter:show_ids"),
            InlineKeyboardButton("🔀 Sort Options", callback_data="filter:open_sort")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_sort_keyboard():
    """Returns sorting options keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("💰 Amount: High ➔ Low", callback_data="sort:amount_desc"),
            InlineKeyboardButton("💰 Amount: Low ➔ High", callback_data="sort:amount_asc")
        ],
        [
            InlineKeyboardButton("🗓️ Date: Newest First", callback_data="sort:date_desc"),
            InlineKeyboardButton("🗓️ Date: Oldest First", callback_data="sort:date_asc")
        ],
        [
            InlineKeyboardButton("🔙 Back to Filters", callback_data="sort:back_filters")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_selection_keyboard(tx_id: int, amount: float):
    """Generates dynamic cafeteria veg item selection keyboard based on paid amount."""
    from services.cafeteria_service import find_exact_items, find_combinations
    exact_items = find_exact_items(amount)
    combos = find_combinations(amount)
    
    keyboard = []
    
    # 1. Exact Matching Veg Items
    for item in exact_items[:4]:
        cb_data = f"cafe_pick:{tx_id}:{item.name}"
        keyboard.append([InlineKeyboardButton(f"🍽️ {item.name} (₹{item.price:.0f})", callback_data=cb_data)])
        
    # 2. Matching Combinations (Item + Packing / Item + Extra Spicy / Pair)
    for combo in combos[:4]:
        short_name = combo.split(' (')[0]
        cb_data = f"cafe_pick:{tx_id}:{short_name[:35]}"
        keyboard.append([InlineKeyboardButton(f"🍴 {combo}", callback_data=cb_data)])
        
    # 3. Category Browser Buttons
    keyboard.append([
        InlineKeyboardButton("🥞 Breakfast", callback_data=f"cafe_cat:{tx_id}:Breakfast"),
        InlineKeyboardButton("🍛 Lunch", callback_data=f"cafe_cat:{tx_id}:Lunch")
    ])
    keyboard.append([
        InlineKeyboardButton("🥘 Veg Dishes", callback_data=f"cafe_cat:{tx_id}:Veg Dishes"),
        InlineKeyboardButton("☕ Snacks & Tea", callback_data=f"cafe_cat:{tx_id}:Snacks & Tea")
    ])
    keyboard.append([
        InlineKeyboardButton("🧃 Juices", callback_data=f"cafe_cat:{tx_id}:Juices"),
        InlineKeyboardButton("🍨 Ice Cream", callback_data=f"cafe_cat:{tx_id}:Ice Cream")
    ])
    keyboard.append([
        InlineKeyboardButton("📦 +₹5 Packing", callback_data=f"cafe_addon:{tx_id}:Packing"),
        InlineKeyboardButton("🌶️ +₹5 Extra Spicy", callback_data=f"cafe_addon:{tx_id}:Extra Spicy")
    ])
    keyboard.append([
        InlineKeyboardButton("⏭️ Skip (General Cafeteria)", callback_data=f"cafe_skip:{tx_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_category_keyboard(tx_id: int, category: str):
    """Shows all items in a specific cafeteria category."""
    from services.cafeteria_service import VEG_MENU
    items = [it for it in VEG_MENU if it.category == category]
    
    keyboard = []
    # Place items in rows of 2 for compact display
    row = []
    for it in items:
        btn = InlineKeyboardButton(f"{it.name} (₹{it.price:.0f})", callback_data=f"cafe_pick:{tx_id}:{it.name}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Suggestions", callback_data=f"cafe_back:{tx_id}")])
    return InlineKeyboardMarkup(keyboard)

