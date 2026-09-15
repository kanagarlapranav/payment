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
            InlineKeyboardButton("🍽️ Change Cafeteria Order", callback_data=f"cafe_edit:{tx_id}"),
            InlineKeyboardButton("💵 Amount", callback_data=f"edit_field:{tx_id}:amount")
        ],
        [
            InlineKeyboardButton("👤 Person Name", callback_data=f"edit_field:{tx_id}:person"),
            InlineKeyboardButton("📅 Date", callback_data=f"edit_field:{tx_id}:date")
        ],
        [
            InlineKeyboardButton("🔄 Type (SENT/RECV)", callback_data=f"edit_field:{tx_id}:type"),
            InlineKeyboardButton("🔢 Ref / UTR", callback_data=f"edit_field:{tx_id}:ref")
        ],
        [
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
    """Generates the initial cafeteria selection menu asking item count and choices."""
    from services.cafeteria_service import find_exact_items
    exact_items = find_exact_items(amount)
    
    keyboard = []
    
    # Mode selection row: 1 Item vs 2 Items vs Plate
    keyboard.append([
        InlineKeyboardButton("1️⃣ 1 Item Mode", callback_data=f"cafe_mode:{tx_id}:1"),
        InlineKeyboardButton("2️⃣ 2 Items Mode", callback_data=f"cafe_mode:{tx_id}:2")
    ])
    keyboard.append([
        InlineKeyboardButton("🛒 Build Plate / Cart", callback_data=f"cafe_mode:{tx_id}:cart"),
        InlineKeyboardButton("🍨 Ice Cream (Enter ₹)", callback_data=f"cafe_custom_prompt:{tx_id}:Ice Cream")
    ])
    
    # If there are exact matching single items for this amount, show them directly
    if exact_items:
        for item in exact_items[:3]:
            cb_data = f"cafe_pick:{tx_id}:{item.name}"
            keyboard.append([InlineKeyboardButton(f"🍽️ {item.name} (Exact ₹{item.price:.0f})", callback_data=cb_data)])
            
    # Quick category buttons
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
        InlineKeyboardButton("✏️ Custom Item & Amount", callback_data=f"cafe_custom_prompt:{tx_id}:Custom"),
        InlineKeyboardButton("⏭️ Skip (General)", callback_data=f"cafe_skip:{tx_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_single_item_keyboard(tx_id: int, amount: float):
    """Shows single item recommendations matching exact or close amount."""
    from services.cafeteria_service import find_exact_items, VEG_MENU
    exact_items = find_exact_items(amount)
    
    keyboard = []
    if exact_items:
        for item in exact_items:
            keyboard.append([InlineKeyboardButton(f"🍽️ {item.name} (₹{item.price:.0f})", callback_data=f"cafe_pick:{tx_id}:{item.name}")])
    else:
        # Show popular single items
        popular = [it for it in VEG_MENU if it.price <= amount + 10][:6]
        for it in popular:
            keyboard.append([InlineKeyboardButton(f"🍽️ {it.name} (₹{it.price:.0f})", callback_data=f"cafe_pick:{tx_id}:{it.name}")])
            
    keyboard.append([
        InlineKeyboardButton("🍨 Enter Ice Cream Amount", callback_data=f"cafe_custom_prompt:{tx_id}:Ice Cream"),
        InlineKeyboardButton("✏️ Custom Item ₹", callback_data=f"cafe_custom_prompt:{tx_id}:Custom")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Cafeteria Menu", callback_data=f"cafe_back:{tx_id}")])
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_two_items_keyboard(tx_id: int, amount: float):
    """Shows 2-item combinations summing to bill amount or 2-item custom split."""
    from services.cafeteria_service import find_combinations
    combos = find_combinations(amount)
    
    keyboard = []
    for combo in combos[:6]:
        short_name = combo.split(' (')[0]
        keyboard.append([InlineKeyboardButton(f"🍴 {combo}", callback_data=f"cafe_pick:{tx_id}:{short_name[:35]}")])
        
    keyboard.append([
        InlineKeyboardButton("🛒 Open Plate Builder", callback_data=f"cafe_mode:{tx_id}:cart"),
        InlineKeyboardButton("✏️ Custom 2-Item ₹", callback_data=f"cafe_custom_prompt:{tx_id}:Two Items")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Cafeteria Menu", callback_data=f"cafe_back:{tx_id}")])
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_cart_keyboard(tx_id: int, bill_amount: float, cart_items: list):
    """Interactive plate builder keyboard for multi-item orders."""
    keyboard = []
    
    # Common quick item additions
    keyboard.append([
        InlineKeyboardButton("➕ Plain Dosa (₹10)", callback_data=f"cafe_cart_add:{tx_id}:Plain Dosa:10"),
        InlineKeyboardButton("➕ Masala Dosa (₹35)", callback_data=f"cafe_cart_add:{tx_id}:Masala Dosa:35")
    ])
    keyboard.append([
        InlineKeyboardButton("➕ Idly 1pc (₹7)", callback_data=f"cafe_cart_add:{tx_id}:Idly:7"),
        InlineKeyboardButton("➕ Poori 1pc (₹7)", callback_data=f"cafe_cart_add:{tx_id}:Poori:7"),
        InlineKeyboardButton("➕ Chapathi (₹6)", callback_data=f"cafe_cart_add:{tx_id}:Chapathi:6")
    ])
    keyboard.append([
        InlineKeyboardButton("➕ Tea (₹8)", callback_data=f"cafe_cart_add:{tx_id}:Tea:8"),
        InlineKeyboardButton("➕ Coffee (₹10)", callback_data=f"cafe_cart_add:{tx_id}:Coffee:10"),
        InlineKeyboardButton("➕ Vada (₹8)", callback_data=f"cafe_cart_add:{tx_id}:Vada:8"),
        InlineKeyboardButton("➕ Lime Juice (₹15)", callback_data=f"cafe_cart_add:{tx_id}:Lime Juice:15")
    ])
    keyboard.append([
        InlineKeyboardButton("➕ Veg Meals (₹45)", callback_data=f"cafe_cart_add:{tx_id}:Veg Meals:45"),
        InlineKeyboardButton("➕ Fried Rice (₹50)", callback_data=f"cafe_cart_add:{tx_id}:Veg Fried Rice:50")
    ])
    keyboard.append([
        InlineKeyboardButton("📦 +₹5 Packing", callback_data=f"cafe_cart_add:{tx_id}:Packing:5"),
        InlineKeyboardButton("🌶️ +₹5 Extra Spicy", callback_data=f"cafe_cart_add:{tx_id}:Extra Spicy:5"),
        InlineKeyboardButton("🍨 +Ice Cream ₹", callback_data=f"cafe_custom_prompt:{tx_id}:Ice Cream")
    ])
    
    if cart_items:
        keyboard.append([
            InlineKeyboardButton(f"✅ Save Plate ({len(cart_items)} items)", callback_data=f"cafe_cart_done:{tx_id}"),
            InlineKeyboardButton("🔄 Clear Plate", callback_data=f"cafe_cart_clear:{tx_id}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🥞 Browse Categories to Add", callback_data=f"cafe_mode:{tx_id}:browse")
        ])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Cafeteria Menu", callback_data=f"cafe_back:{tx_id}")])
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_category_keyboard(tx_id: int, category: str):
    """Shows all items in a specific cafeteria category."""
    from services.cafeteria_service import get_all_menu_items
    items = [it for it in get_all_menu_items() if it.category == category]
    
    keyboard = []
    row = []
    for it in items:
        btn = InlineKeyboardButton(f"{it.name} (₹{it.price:.0f})", callback_data=f"cafe_pick:{tx_id}:{it.name}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    # Special button in Ice Cream category
    if category == "Ice Cream":
        keyboard.append([InlineKeyboardButton("🍨 Enter Custom Ice Cream Price", callback_data=f"cafe_custom_prompt:{tx_id}:Ice Cream")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Cafeteria Menu", callback_data=f"cafe_back:{tx_id}")])
    return InlineKeyboardMarkup(keyboard)

def get_cafeteria_tagged_keyboard(tx_id: int):
    """Inline keyboard attached to confirmed/tagged cafeteria orders."""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit Cafeteria Items", callback_data=f"cafe_edit:{tx_id}"),
            InlineKeyboardButton("📦 Add +₹5 Packing", callback_data=f"cafe_addon:{tx_id}:Packing")
        ],
        [
            InlineKeyboardButton("📊 Cafeteria Spends", callback_data="cafe_stats"),
            InlineKeyboardButton("📋 View Menu", callback_data="cafe_view_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_menu_view_keyboard():
    """Inline keyboard for /menu display."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Menu Item", callback_data="cafe_menu_add_prompt"),
            InlineKeyboardButton("🗑️ Manage / Delete Item", callback_data="cafe_menu_del_prompt")
        ],
        [
            InlineKeyboardButton("📊 Cafeteria Analytics", callback_data="cafe_stats"),
            InlineKeyboardButton("✏️ Edit Last Order", callback_data="cafe_edit_last")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)



