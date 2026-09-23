from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_home_menu_keyboard():
    """Returns the 6-button Home Menu grid: Balance, Today, History, Add, Stats, More."""
    keyboard = [
        [
            InlineKeyboardButton("💰 Balance", callback_data="nav:balance"),
            InlineKeyboardButton("📅 Today", callback_data="nav:today")
        ],
        [
            InlineKeyboardButton("🧾 History", callback_data="nav:history:1:ALL"),
            InlineKeyboardButton("➕ Add", callback_data="nav:add")
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="nav:stats"),
            InlineKeyboardButton("⚙️ More", callback_data="nav:more")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_add_menu_keyboard():
    """Returns the Add Transaction menu: Scan Receipt, Manual Entry, Natural Text, Quick Add."""
    keyboard = [
        [
            InlineKeyboardButton("📸 Scan Receipt", callback_data="nav:add_scan"),
            InlineKeyboardButton("⌨️ Manual Entry", callback_data="nav:add_manual")
        ],
        [
            InlineKeyboardButton("💬 Natural Text", callback_data="nav:add_text"),
            InlineKeyboardButton("⚡ Quick Add", callback_data="nav:quickadd")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_more_menu_keyboard():
    """Returns the More submenu grouping advanced features."""
    keyboard = [
        [
            InlineKeyboardButton("🎯 Budgets", callback_data="nav:budget"),
            InlineKeyboardButton("🍽️ Cafeteria", callback_data="nav:cafe")
        ],
        [
            InlineKeyboardButton("🔄 Recurring Dues", callback_data="nav:recurring"),
            InlineKeyboardButton("📊 Month Closing", callback_data="nav:month_close")
        ],
        [
            InlineKeyboardButton("☁️ Backup Status", callback_data="nav:backup_status"),
            InlineKeyboardButton("🌐 Web Dashboard", callback_data="nav:dash_info")
        ],
        [
            InlineKeyboardButton("👥 Contacts", callback_data="nav:contacts"),
            InlineKeyboardButton("ℹ️ Help & Commands", callback_data="nav:help_info")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Backward-compatibility alias
get_settings_menu_keyboard = get_more_menu_keyboard

def get_recurring_menu_keyboard(upcoming_items: list = None):
    """Returns recurring payments overview keyboard with quick pay/skip buttons."""
    keyboard = []
    if upcoming_items:
        for it in upcoming_items[:4]:
            rec_id = it['id']
            payee = (it.get('payee_name') or 'Due')[:12]
            try:
                amt = int(float(it.get('amount', 0)))
            except Exception:
                amt = 0
            keyboard.append([
                InlineKeyboardButton(f"✅ Pay #{rec_id} {payee} (₹{amt})", callback_data=f"rec_paid:{rec_id}"),
                InlineKeyboardButton(f"⏭️ Skip", callback_data=f"rec_skip:{rec_id}")
            ])
    keyboard.append([
        InlineKeyboardButton("➕ Add Recurring", callback_data="nav:rec_add"),
        InlineKeyboardButton("📋 All Recurring", callback_data="nav:rec_all")
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="nav:more")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_recurring_detail_keyboard(rec_id: int, status: str = "ACTIVE"):
    """Returns action keyboard for a single recurring payment."""
    toggle_text = "⏸️ Pause" if status == "ACTIVE" else "▶️ Resume"
    toggle_cb = f"rec_pause:{rec_id}" if status == "ACTIVE" else f"rec_resume:{rec_id}"
    keyboard = [
        [
            InlineKeyboardButton("✅ Mark Paid", callback_data=f"rec_paid:{rec_id}"),
            InlineKeyboardButton("⏭️ Skip Cycle", callback_data=f"rec_skip:{rec_id}")
        ],
        [
            InlineKeyboardButton(toggle_text, callback_data=toggle_cb),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"rec_del:{rec_id}")
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="nav:recurring")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_monthly_closing_keyboard(year: int, month: int, is_closed: bool = False):
    """Returns the Monthly Closing Review keyboard with Mark Reviewed and Export actions."""
    # Previous month
    if month == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, month - 1
    # Next month
    if month == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, month + 1
        
    review_label = "🔄 Re-Review Month" if is_closed else "✅ Mark Month Reviewed"
    keyboard = [
        [
            InlineKeyboardButton(review_label, callback_data=f"close_month:{year}:{month}"),
            InlineKeyboardButton("📥 Export Statement", callback_data="export_file:excel")
        ],
        [
            InlineKeyboardButton("◀ Prev", callback_data=f"nav:month_close:{prev_y}:{prev_m}"),
            InlineKeyboardButton("Next ▶", callback_data=f"nav:month_close:{next_y}:{next_m}")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:more")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_transaction_detail_keyboard(tx_id: int):
    """Returns the Transaction Detail keyboard with Edit, Delete, Duplicate, and Back."""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit_tx:{tx_id}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_tx:{tx_id}")
        ],
        [
            InlineKeyboardButton("📋 Duplicate", callback_data=f"dup_tx:{tx_id}"),
            InlineKeyboardButton("⬅️ Back", callback_data="nav:history:1:ALL")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_backup_status_keyboard():
    """Returns Backup Status action buttons."""
    keyboard = [
        [
            InlineKeyboardButton("🔄 Backup Now", callback_data="backup_now"),
            InlineKeyboardButton("📥 Restore Backup", callback_data="nav:restore_info")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_json_import_confirm_keyboard(token: str):
    """Returns Confirm / Cancel buttons for a staged JSON import."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Import", callback_data=f"json_import_confirm:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"json_import_cancel:{token}")
        ]
    ])

def get_back_to_menu_keyboard(extra_row=None):
    """Returns a keyboard with a back-to-menu button and optional extra buttons."""
    keyboard = []
    if extra_row:
        keyboard.append(extra_row)
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")])
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_card_keyboard(pending_id: str, duplicate_warning: bool = False):
    """Returns the polished confirmation card keyboard: Save, Edit, Category, Cancel."""
    save_label = "⚠️ Save Anyway" if duplicate_warning else "✅ Save"
    keyboard = [
        [
            InlineKeyboardButton(save_label, callback_data=f"save_p:{pending_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit_p:{pending_id}")
        ],
        [
            InlineKeyboardButton("🏷️ Category", callback_data=f"cat_p:{pending_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_p:{pending_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_edit_pending_fields_keyboard(pending_id: str):
    """Returns per-field buttons for editing a detected receipt before saving."""
    keyboard = [
        [
            InlineKeyboardButton("💵 Amount", callback_data=f"ep_field:{pending_id}:amount"),
            InlineKeyboardButton("👤 Payee / Name", callback_data=f"ep_field:{pending_id}:person")
        ],
        [
            InlineKeyboardButton("📅 Date", callback_data=f"ep_field:{pending_id}:date"),
            InlineKeyboardButton("🔄 Type (SENT/RECV)", callback_data=f"ep_field:{pending_id}:type")
        ],
        [
            InlineKeyboardButton("🔙 Back to Receipt", callback_data=f"ep_back:{pending_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_category_picker_keyboard(pending_id: str):
    """Returns a 2-column grid of common categories to tap and categorize instantly."""
    categories = [
        ("🍔 Food & Dining", "Food & Dining"),
        ("🛒 Groceries", "Groceries"),
        ("🚗 Transport", "Transport"),
        ("💡 Utilities", "Bills & Utilities"),
        ("🛍️ Shopping", "Shopping"),
        ("🍿 Entertainment", "Entertainment"),
        ("🏥 Healthcare", "Healthcare"),
        ("💼 Salary/Income", "Salary"),
        ("📦 General", "General")
    ]
    keyboard = []
    row = []
    for label, cat in categories:
        row.append(InlineKeyboardButton(label, callback_data=f"set_pcat:{pending_id}:{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Back to Receipt", callback_data=f"ep_back:{pending_id}")])
    return InlineKeyboardMarkup(keyboard)

# Aliases
get_receipt_confirm_keyboard = get_confirmation_card_keyboard
get_receipt_edit_fields_keyboard = get_edit_pending_fields_keyboard

def get_quick_undo_keyboard(tx_id: int):
    """Returns an inline Undo button for a newly saved transaction."""
    keyboard = [
        [
            InlineKeyboardButton("↩️ Undo", callback_data=f"undo_tx:{tx_id}"),
            InlineKeyboardButton("🏠 Menu", callback_data="nav:home")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_quick_add_keyboard(top_payees: list = None):
    """Returns one-tap entry shortcuts for common payees/items."""
    keyboard = []
    
    # 1. If top payees available, display top 3 payees
    if top_payees:
        for p in top_payees[:3]:
            name = p.get('person_name') or 'Payee'
            short_name = name[:14]
            keyboard.append([
                InlineKeyboardButton(f"💸 Sent to {short_name}", callback_data=f"qa_payee:{short_name[:12]}:SENT"),
                InlineKeyboardButton(f"💰 Recv from {short_name}", callback_data=f"qa_payee:{short_name[:12]}:RECEIVED")
            ])
            
    # 2. Common quick cafeteria items
    keyboard.append([
        InlineKeyboardButton("☕ Coffee ₹10", callback_data="quick_add:10:Coffee:Food & Dining"),
        InlineKeyboardButton("🥞 Plain Dosa ₹10", callback_data="quick_add:10:Plain Dosa:Food & Dining")
    ])
    keyboard.append([
        InlineKeyboardButton("🍛 Veg Meals ₹45", callback_data="quick_add:45:Veg Meals:Food & Dining"),
        InlineKeyboardButton("🧃 Lime Juice ₹15", callback_data="quick_add:15:Lime Juice:Food & Dining")
    ])
    keyboard.append([
        InlineKeyboardButton("🍽️ Masala Dosa ₹35", callback_data="quick_add:35:Masala Dosa:Food & Dining"),
        InlineKeyboardButton("🍨 Ice Cream ₹20", callback_data="quick_add:20:Ice Cream:Food & Dining")
    ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_history_paginated_keyboard(page: int, total_pages: int, filter_type: str = "ALL", tx_rows: list = None, sort_by: str = "date_desc"):
    """Returns interactive pagination buttons, filter chips, sort toggle, and row tap shortcuts for /history."""
    keyboard = []
    sort_by = sort_by or "date_desc"
    
    # 1. Row buttons to open detail screen for items on this page
    if tx_rows:
        row_buttons = []
        start_num = (page - 1) * 5 + 1
        for idx, r in enumerate(tx_rows[:6], start=start_num):
            badge = "🟢" if r.get('transaction_type') == 'RECEIVED' else "🔴"
            try:
                amt = int(float(r.get('amount', 0)))
            except Exception:
                amt = 0
            label = f"#{idx} {badge} ₹{amt}"
            row_buttons.append(InlineKeyboardButton(label, callback_data=f"tx_view:{r['id']}"))
            if len(row_buttons) == 2:
                keyboard.append(row_buttons)
                row_buttons = []
        if row_buttons:
            keyboard.append(row_buttons)
            
    # 2. Filter chips row
    all_label = "● All" if filter_type == "ALL" else "All"
    sent_label = "● 🔴 Sent" if filter_type == "SENT" else "🔴 Sent"
    recv_label = "● 🟢 Recv" if filter_type == "RECEIVED" else "🟢 Recv"
    keyboard.append([
        InlineKeyboardButton(all_label, callback_data=f"nav:history:1:ALL:{sort_by}"),
        InlineKeyboardButton(sent_label, callback_data=f"nav:history:1:SENT:{sort_by}"),
        InlineKeyboardButton(recv_label, callback_data=f"nav:history:1:RECEIVED:{sort_by}")
    ])
    
    # 3. Sort Order Toggle Row
    if sort_by == "date_asc":
        sort_btn = InlineKeyboardButton("⬆️ Oldest First (ASC)", callback_data=f"nav:history:1:{filter_type}:id_desc")
    elif sort_by in ("id_desc", "created_desc"):
        sort_btn = InlineKeyboardButton("🆔 ID Order (ID DESC)", callback_data=f"nav:history:1:{filter_type}:date_desc")
    else:
        sort_btn = InlineKeyboardButton("⬇️ Newest First (DESC)", callback_data=f"nav:history:1:{filter_type}:date_asc")
    keyboard.append([sort_btn])

    # 4. Navigation row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f"nav:history:{page-1}:{filter_type}:{sort_by}"))
    nav_row.append(InlineKeyboardButton(f"{page} / {max(1, total_pages)}", callback_data="nav:history_noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f"nav:history:{page+1}:{filter_type}:{sort_by}"))
    keyboard.append(nav_row)
    
    # 5. Back to Menu
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")])
    return InlineKeyboardMarkup(keyboard)

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

def get_undo_keyboard():
    """Returns an inline keyboard with an Undo button."""
    keyboard = [
        [
            InlineKeyboardButton("↩️ Undo", callback_data="undo_action")
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


def get_model_selection_keyboard(current_model: str = "AUTO") -> InlineKeyboardMarkup:
    """Returns an inline keyboard allowing the user to select the preferred Gemini model or Auto-Failover."""
    curr = (current_model or "AUTO").strip()

    auto_label = "✅ ⚡ Auto-Failover (3.8 ➔ 3.7 ➔ 3.6 ➔ 3.5)" if curr == "AUTO" else "⚡ Auto-Failover (3.8 ➔ 3.7 ➔ 3.6 ➔ 3.5)"
    m38_label = "✅ 1️⃣ 3.8 Flash" if curr == "gemini-3.8-flash" else "1️⃣ 3.8 Flash"
    m37_label = "✅ 2️⃣ 3.7 Flash" if curr == "gemini-3.7-flash" else "2️⃣ 3.7 Flash"
    m36_label = "✅ 3️⃣ 3.6 Flash" if curr == "gemini-3.6-flash" else "3️⃣ 3.6 Flash"
    m35_label = "✅ 4️⃣ 3.5 Flash Lite" if curr == "gemini-3.5-flash-lite" else "4️⃣ 3.5 Flash Lite"

    keyboard = [
        [InlineKeyboardButton(auto_label, callback_data="set_model:AUTO")],
        [
            InlineKeyboardButton(m38_label, callback_data="set_model:gemini-3.8-flash"),
            InlineKeyboardButton(m37_label, callback_data="set_model:gemini-3.7-flash")
        ],
        [
            InlineKeyboardButton(m36_label, callback_data="set_model:gemini-3.6-flash"),
            InlineKeyboardButton(m35_label, callback_data="set_model:gemini-3.5-flash-lite")
        ],
        [InlineKeyboardButton("🔄 Refresh Quota & Pool Status", callback_data="refresh_gemini")]
    ]
    return InlineKeyboardMarkup(keyboard)




