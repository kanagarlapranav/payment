"""
Cafeteria callback handlers and dispatch table.
"""

import html
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database.queries import (
    get_transaction_by_id,
    update_transaction,
    get_cafeteria_transactions,
)
from database.db import get_custom_menu_items, delete_custom_menu_item_by_id
from bot.keyboards import (
    get_cafeteria_tagged_keyboard,
    get_cafeteria_single_item_keyboard,
    get_cafeteria_two_items_keyboard,
    get_cafeteria_cart_keyboard,
    get_cafeteria_selection_keyboard,
    get_cafeteria_category_keyboard,
    get_menu_view_keyboard,
    get_back_to_menu_keyboard,
)
from utils.currency import format_currency

logger = logging.getLogger(__name__)


def parse_int_arg(parts: list, idx: int):
    if len(parts) > idx and parts[idx].isdigit():
        return int(parts[idx])
    return None


def parse_float_arg(parts: list, idx: int):
    if len(parts) > idx:
        try:
            return float(parts[idx])
        except (ValueError, TypeError):
            return None
    return None


async def handle_cafe_pick(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None or len(parts) < 3:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    item_name = parts[2]
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    if tx:
        new_name = f"VIKRAMAN NAIR K (Cafeteria: {item_name})"
        update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'}, workspace_id=ws_id)
        from services.task_manager import schedule_debounced_backup
        schedule_debounced_backup(context.bot)
        amt_s = format_currency(tx['amount'])
        bal_s = format_currency(tx['balance_after'])
        await query.edit_message_text(
            f"🍽️ <b>Cafeteria Order Tagged!</b>\n\n"
            f"• <b>Ordered Item:</b> 🍽️ <b>{html.escape(item_name)}</b>\n"
            f"• <b>Merchant:</b> VIKRAMAN NAIR K\n"
            f"• <b>Amount:</b> <b>{html.escape(amt_s)}</b>\n"
            f"• <b>Category:</b> 🍔 Food & Dining\n"
            f"• <b>Balance:</b> {html.escape(bal_s)}\n\n"
            f"✅ Successfully saved to your transaction record!",
            reply_markup=get_cafeteria_tagged_keyboard(tx_id),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text(f"🍽️ Tagged order: <b>{html.escape(item_name)}</b>", parse_mode='HTML')


async def handle_cafe_mode(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None or len(parts) < 3:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    mode = parts[2]
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    amt = float(tx['amount']) if tx else 0.0

    if mode == "1":
        await query.edit_message_text(
            f"🍽️ <b>Single Item Selection (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"Pick the single item you ordered below or enter custom amount:",
            reply_markup=get_cafeteria_single_item_keyboard(tx_id, amt),
            parse_mode='HTML'
        )
    elif mode == "2":
        await query.edit_message_text(
            f"🍽️ <b>Two Items Mode (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"Pick your combination below or open the plate builder:",
            reply_markup=get_cafeteria_two_items_keyboard(tx_id, amt),
            parse_mode='HTML'
        )
    elif mode == "cart":
        cart = context.user_data.get(f'cafe_cart_{tx_id}', [])
        total_cart = sum(it['price'] for it in cart)
        cart_lines = "\n".join([f"• {it['name']} — ₹{it['price']:.0f}" for it in cart]) if cart else "<i>(Plate is empty. Tap items below to add)</i>"
        await query.edit_message_text(
            f"🛒 <b>CAFETERIA PLATE BUILDER</b>\n"
            f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
            f"<b>Items in Plate:</b>\n{cart_lines}\n\n"
            f"<b>Plate Sum:</b> <b>{html.escape(format_currency(total_cart))}</b> / {html.escape(format_currency(amt))}\n\n"
            f"Tap items below to add to your plate:",
            reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, cart),
            parse_mode='HTML'
        )
    elif mode == "browse":
        await query.edit_message_text(
            f"📋 <b>Browse Cafeteria Menu (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"Select a category below to see all items:",
            reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
            parse_mode='HTML'
        )


async def handle_cafe_custom_prompt(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None or len(parts) < 3:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    item_type = parts[2]
    context.user_data['action'] = 'waiting_cafe_custom_amount'
    context.user_data['cafe_tx_id'] = tx_id
    context.user_data['cafe_item'] = item_type
    icon = "🍨" if "ice" in item_type.lower() else "✏️"
    await query.edit_message_text(
        f"{icon} <b>Enter Amount for {html.escape(item_type)}:</b>\n\n"
        f"Please reply with the amount (e.g. <code>40</code>, <code>60</code>, <code>120</code>) or item name with price:",
        parse_mode='HTML'
    )


async def handle_cafe_cart_add(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    item_price = parse_float_arg(parts, 3)
    if tx_id is None or item_price is None or len(parts) < 4:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    item_name = parts[2]
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    amt = float(tx['amount']) if tx else 0.0

    cart_key = f'cafe_cart_{tx_id}'
    if cart_key not in context.user_data:
        context.user_data[cart_key] = []
    context.user_data[cart_key].append({'name': item_name, 'price': item_price})

    cart = context.user_data[cart_key]
    total_cart = sum(it['price'] for it in cart)
    cart_lines = "\n".join([f"• {it['name']} — ₹{it['price']:.0f}" for it in cart])

    await query.edit_message_text(
        f"🛒 <b>CAFETERIA PLATE BUILDER</b>\n"
        f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
        f"<b>Items in Plate ({len(cart)}):</b>\n{cart_lines}\n\n"
        f"<b>Plate Sum:</b> <b>{html.escape(format_currency(total_cart))}</b> / {html.escape(format_currency(amt))}\n\n"
        f"Tap more items or tap <b>Save Plate</b> when done:",
        reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, cart),
        parse_mode='HTML'
    )


async def handle_cafe_cart_clear(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    amt = float(tx['amount']) if tx else 0.0
    context.user_data[f'cafe_cart_{tx_id}'] = []
    await query.edit_message_text(
        f"🛒 <b>Plate Cleared!</b>\n"
        f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
        f"Tap items below to build your plate:",
        reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, []),
        parse_mode='HTML'
    )


async def handle_cafe_cart_done(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    cart = context.user_data.pop(f'cafe_cart_{tx_id}', [])
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    if tx and cart:
        item_names = " + ".join([it['name'] for it in cart])
        new_name = f"VIKRAMAN NAIR K (Cafeteria: {item_names})"
        update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'}, workspace_id=ws_id)
        from services.task_manager import schedule_debounced_backup
        schedule_debounced_backup(context.bot)
        amt_s = format_currency(tx['amount'])
        bal_s = format_currency(tx['balance_after'])
        await query.edit_message_text(
            f"🍽️ <b>Cafeteria Plate Saved!</b>\n\n"
            f"• <b>Ordered Items:</b> 🍽️ <b>{html.escape(item_names)}</b>\n"
            f"• <b>Merchant:</b> VIKRAMAN NAIR K\n"
            f"• <b>Bill Amount:</b> <b>{html.escape(amt_s)}</b>\n"
            f"• <b>Category:</b> 🍔 Food & Dining\n"
            f"• <b>Balance:</b> {html.escape(bal_s)}\n\n"
            f"✅ Successfully saved to your transaction record!",
            reply_markup=get_cafeteria_tagged_keyboard(tx_id),
            parse_mode='HTML'
        )
    elif tx:
        await query.edit_message_text(
            "⚠️ No items were in the plate. Choose an item below:",
            reply_markup=get_cafeteria_selection_keyboard(tx_id, float(tx['amount'])),
            parse_mode='HTML'
        )


async def handle_cafe_cat(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None or len(parts) < 3:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    cat_name = parts[2]
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    amt_str = format_currency(tx['amount']) if tx else ""
    await query.edit_message_text(
        f"🍽️ <b>Cafeteria Menu — {html.escape(cat_name)}</b>\n"
        f"Bill Amount: <b>{html.escape(amt_str)}</b>\n\n"
        f"Tap any item to tag it:",
        reply_markup=get_cafeteria_category_keyboard(tx_id, cat_name),
        parse_mode='HTML'
    )


async def handle_cafe_back(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    amt = float(tx['amount']) if tx else 0.0
    await query.edit_message_text(
        f"🍽️ <b>Cafeteria Menu Selection (Paid {html.escape(format_currency(amt))}):</b>\n\n"
        f"<b>How many items or what did you order?</b>\n"
        f"Select an option below to tag your order:",
        reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
        parse_mode='HTML'
    )


async def handle_cafe_addon(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None or len(parts) < 3:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    addon = parts[2]
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    if tx:
        current_name = tx['person_name'] or "VIKRAMAN NAIR K (Cafeteria)"
        updated_name = f"{current_name} + {addon}"
        update_transaction(tx_id, {'person_name': updated_name, 'category': 'Food & Dining'}, workspace_id=ws_id)
        await query.edit_message_text(
            f"🍽️ <b>Add-on Tagged:</b> +₹5 {html.escape(addon)}\n"
            f"• Updated Merchant: {html.escape(updated_name)}\n"
            f"• Total Amount: <b>{html.escape(format_currency(tx['amount']))}</b>\n\n"
            f"✅ Updated record!",
            reply_markup=get_cafeteria_tagged_keyboard(tx_id),
            parse_mode='HTML'
        )


async def handle_cafe_edit(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    if tx:
        amt = float(tx['amount'])
        await query.edit_message_text(
            f"✏️ <b>Edit Cafeteria Order for Transaction #{tx_id} (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"<b>How many items or what did you order?</b>\n"
            f"Select an option below to update your order:",
            reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text("❌ Transaction not found.")


async def handle_cafe_stats(query, context, parts, ws_id, ws_ctx, update=None):
    if not query.message:
        return
    from services.cafeteria_service import format_cafeteria_stats
    stats_text = format_cafeteria_stats(workspace_id=ws_id)
    await query.message.reply_text(stats_text, parse_mode='HTML')


async def handle_cafe_view_menu(query, context, parts, ws_id, ws_ctx, update=None):
    if not query.message:
        return
    from services.cafeteria_service import format_full_menu
    menu_text = format_full_menu(workspace_id=ws_id)
    await query.message.reply_text(menu_text, reply_markup=get_menu_view_keyboard(), parse_mode='HTML')


async def handle_cafe_menu_add_prompt(query, context, parts, ws_id, ws_ctx, update=None):
    if not query.message:
        return
    context.user_data['action'] = 'waiting_add_menu_item'
    await query.message.reply_text(
        "➕ <b>Add Custom Menu Item</b>\n\n"
        "Please send the item details in this format:\n"
        "<code>Item Name, Price, Category</code>\n\n"
        "<i>Examples:</i>\n"
        "• <code>Paneer Roll, 45, Snacks</code>\n"
        "• <code>Mango Lassi, 35, Beverages</code>\n"
        "• <code>Veg Noodles, 50, Chinese</code>",
        parse_mode='HTML'
    )


async def handle_cafe_menu_del_prompt(query, context, parts, ws_id, ws_ctx, update=None):
    if not query.message:
        return
    custom_items = get_custom_menu_items(workspace_id=ws_id)
    if not custom_items:
        await query.message.reply_text(
            "ℹ️ No custom menu items found to delete. Predefined standard items cannot be removed.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
    else:
        keyboard = []
        for it in custom_items:
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete {it['name']} (₹{it['price']:.0f})", callback_data=f"cafe_del_item:{it['id']}")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cafe_del_cancel")])
        await query.message.reply_text(
            "🗑️ <b>Select Custom Menu Item to Remove:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )


async def handle_cafe_del_item(query, context, parts, ws_id, ws_ctx, update=None):
    item_id = parse_int_arg(parts, 1)
    if item_id is None:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    success, name = delete_custom_menu_item_by_id(item_id, workspace_id=ws_id)
    if success:
        await query.edit_message_text(f"✅ Removed custom item: <b>{html.escape(name)}</b> from cafeteria menu.", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
    else:
        await query.edit_message_text("❌ Failed to remove menu item.", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')


async def handle_cafe_del_cancel(query, context, parts, ws_id, ws_ctx, update=None):
    await query.edit_message_text("❌ Menu item deletion cancelled.", reply_markup=get_back_to_menu_keyboard())


async def handle_cafe_edit_last(query, context, parts, ws_id, ws_ctx, update=None):
    if not query.message:
        return
    cafe_txs = get_cafeteria_transactions(limit=1, workspace_id=ws_id)
    if cafe_txs:
        tx = cafe_txs[0]
        tx_id = tx['id']
        amt = float(tx['amount'])
        await query.message.reply_text(
            f"✏️ <b>Edit Cafeteria Order #{tx_id} (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"Select an option below to update your order:",
            reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
            parse_mode='HTML'
        )
    else:
        await query.message.reply_text("❌ No recent cafeteria payments found.")


async def handle_cafe_skip(query, context, parts, ws_id, ws_ctx, update=None):
    tx_id = parse_int_arg(parts, 1)
    if tx_id is None:
        await query.answer("❌ Invalid button data.", show_alert=True)
        return
    tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
    amt_s = format_currency(tx['amount']) if tx else ""
    await query.edit_message_text(
        f"✅ <b>Cafeteria Payment Recorded</b> ({html.escape(amt_s)})\n"
        f"Tagged as: 🍔 <b>Food & Dining</b> (General)",
        reply_markup=get_cafeteria_tagged_keyboard(tx_id),
        parse_mode='HTML'
    )


CAFE_DISPATCH_TABLE = {
    "cafe_pick": handle_cafe_pick,
    "cafe_mode": handle_cafe_mode,
    "cafe_custom_prompt": handle_cafe_custom_prompt,
    "cafe_cart_add": handle_cafe_cart_add,
    "cafe_cart_clear": handle_cafe_cart_clear,
    "cafe_cart_done": handle_cafe_cart_done,
    "cafe_cat": handle_cafe_cat,
    "cafe_back": handle_cafe_back,
    "cafe_addon": handle_cafe_addon,
    "cafe_edit": handle_cafe_edit,
    "cafe_stats": handle_cafe_stats,
    "cafe_view_menu": handle_cafe_view_menu,
    "cafe_menu_add_prompt": handle_cafe_menu_add_prompt,
    "cafe_menu_del_prompt": handle_cafe_menu_del_prompt,
    "cafe_del_item": handle_cafe_del_item,
    "cafe_del_cancel": handle_cafe_del_cancel,
    "cafe_edit_last": handle_cafe_edit_last,
    "cafe_skip": handle_cafe_skip,
}


async def handle_cafe_callback(query, context, action, parts, ws_id, ws_ctx, update=None) -> bool:
    """Dispatches cafeteria callback queries via the dispatch table."""
    handler = CAFE_DISPATCH_TABLE.get(action)
    if handler:
        await handler(query, context, parts, ws_id, ws_ctx, update)
        return True
    return False
