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
    """Returns a list of buttons for selecting a recent transaction."""
    from utils.currency import format_currency
    keyboard = []
    for t in transactions[:6]: # Show top 6
        person = t['person_name'] or t['transaction_type']
        date_str = str(t['transaction_date']) if t['transaction_date'] else ""
        if len(date_str) > 5:
            date_display = date_str[5:] # e.g. 09-05
        else:
            date_display = date_str
        amt = format_currency(t['amount'])
        btn_label = f"{t['transaction_type'][0]}: {person[:12]} - {amt} ({date_display})"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"{action_prefix}:{t['id']}")])
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

