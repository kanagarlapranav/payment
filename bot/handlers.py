from telegram import Update
from telegram.ext import ContextTypes
import os
import uuid
import json
import asyncio
import html
import re
from datetime import timedelta
from config import TELEGRAM_USER_ID, IMAGE_DIR, logger
from bot.commands import is_authorized
from bot.keyboards import (
    get_confirmation_keyboard, get_edit_fields_keyboard, get_delete_confirm_keyboard,
    get_filter_keyboard, get_sort_keyboard
)
from ocr.extractor import perform_ocr
from ocr.gemini_vision import is_gemini_available, extract_transaction_with_gemini
from services.gdrive_service import is_gdrive_available, upload_receipt_to_drive, upload_backup_to_drive
from services.transaction_service import process_transaction, commit_transaction
from services.balance_service import recalculate_all_balances, resequence_transaction_ids
from services.backup_service import backup_to_telegram
from database.queries import (
    get_transaction_by_id, get_transaction_by_reference, update_transaction, delete_transaction,
    search_transactions, get_monthly_summary, get_recent_transactions, get_balance_setting
)
from utils.currency import parse_amount, format_currency
from utils.dates import parse_date, get_current_time_in_tz, format_display_date

# In-memory store for pending transactions awaiting confirmation
pending_transactions = {}

async def deliver_response(status_msg, message, text: str, reply_markup=None, parse_mode='HTML'):
    """Safely updates status_msg or sends a new reply if edit_text fails, ensuring no stuck status messages."""
    try:
        await status_msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as err:
        logger.warning(f"Could not edit status message with parse_mode={parse_mode}: {err}. Retrying with clean text...")
        clean_text = re.sub(r'<[^>]+>', '', text)
        try:
            await status_msg.edit_text(clean_text, reply_markup=reply_markup)
            return
        except Exception as edit_err:
            logger.warning(f"Could not edit status message without formatting ({edit_err}); deleting status and sending reply.")
            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                try:
                    await message.reply_text(clean_text, reply_markup=reply_markup)
                except Exception as final_err:
                    logger.error(f"Failed to deliver message: {final_err}")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming images (screenshots) with Gemini Vision AI + RapidOCR fallback."""
    if not await is_authorized(update): return
    
    message = update.message
    chat_id = str(message.chat_id)
    message_id = str(message.message_id)
    caption = message.caption or ""
    
    # Get the file (photo or document)
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
    else:
        return # Ignore non-images
        
    status_msg = await message.reply_text("🔍 Analyzing screenshot...")
    
    try:
        # Download image with resilient timeout and retry
        ext = ".jpg" # Default
        if message.document:
            ext = os.path.splitext(message.document.file_name)[1] or ".jpg"
            
        filename = f"tx_{uuid.uuid4().hex[:8]}{ext}"
        image_path = IMAGE_DIR / filename
        
        for attempt in range(2):
            try:
                file = await context.bot.get_file(file_id, read_timeout=60.0, connect_timeout=30.0)
                await file.download_to_drive(custom_path=image_path, read_timeout=60.0, connect_timeout=30.0)
                break
            except Exception as dl_err:
                if attempt == 1:
                    raise dl_err
                await asyncio.sleep(1)
        
        # Send typing action
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass

        transaction = None
        confidence = 0

        # Tier 1: Try Google Gemini Vision AI if API key is configured
        if is_gemini_available():
            try:
                logger.info("Attempting Gemini Vision extraction...")
                g_tx, g_conf = await asyncio.to_thread(extract_transaction_with_gemini, str(image_path), caption)
                if g_tx and g_conf >= 50:
                    transaction = g_tx
                    confidence = g_conf
                    transaction.telegram_message_id = message_id
                    transaction.telegram_chat_id = chat_id
                    transaction.original_image_path = str(image_path)
            except Exception as gem_err:
                logger.warning(f"Gemini Vision error, falling back to local OCR: {gem_err}")

        # Tier 2: Fallback to local RapidOCR + Regex Heuristic Parser
        if not transaction or not transaction.amount:
            try:
                raw_text = await asyncio.wait_for(
                    asyncio.to_thread(perform_ocr, str(image_path)),
                    timeout=60.0  # Hard 60-second limit
                )
            except asyncio.TimeoutError:
                logger.error(f"OCR timed out after 60s on {image_path}")
                await deliver_response(status_msg, message, "⏱️ OCR took too long. Please try a clearer/smaller screenshot.")
                try:
                    if os.path.exists(image_path):
                        os.remove(image_path)
                except OSError:
                    pass
                return

            if not raw_text or not raw_text.strip():
                await deliver_response(status_msg, message, "❌ Could not extract any readable text from the image. Please upload a clearer screenshot.")
                try:
                    if os.path.exists(image_path):
                        os.remove(image_path)
                except OSError:
                    pass
                return

            transaction, confidence = process_transaction(raw_text, str(image_path), message_id, chat_id, caption=caption)

        # Always clean up temporary image file immediately after text/data extraction
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
                logger.info(f"Cleaned up temporary image after OCR: {image_path}")
        except OSError as cleanup_err:
            logger.warning(f"Could not delete temp image {image_path}: {cleanup_err}")
        
        # Basic validation
        if not transaction.amount or transaction.amount <= 0:
            await deliver_response(status_msg, message, "⚠️ Could not detect a valid amount. Please provide a clearer screenshot or enter manually.")
            return
            
        if not transaction.transaction_type:
            await deliver_response(status_msg, message, f"⚠️ Transaction type (SENT/RECEIVED) could not be determined reliably.\nAmount found: {format_currency(transaction.amount)}")
            return
            
        # Check if already recorded (duplicate prevention with smart auto-update if details differ)
        if transaction.reference_number:
            existing = get_transaction_by_reference(transaction.reference_number)
            if existing:
                is_diff = (
                    abs(float(existing['amount']) - float(transaction.amount)) > 0.01 or
                    (transaction.person_name and transaction.person_name != "Unknown" and existing['person_name'] != transaction.person_name)
                )
                if is_diff and confidence >= 70:
                    tx_id = existing['id']
                    from database.queries import update_transaction, get_transaction_by_id
                    from services.balance_service import recalculate_all_balances
                    
                    updates = {
                        'amount': transaction.amount,
                        'transaction_type': transaction.transaction_type,
                        'person_name': transaction.person_name,
                        'payment_app': transaction.payment_app or existing['payment_app'],
                        'category': transaction.category or existing['category']
                    }
                    if transaction.transaction_type == 'SENT':
                        updates['recipient_name'] = transaction.person_name
                    else:
                        updates['sender_name'] = transaction.person_name

                    update_transaction(tx_id, updates)
                    recalculate_all_balances()
                    
                    updated_tx = get_transaction_by_id(tx_id)
                    transaction.id = tx_id
                    transaction.balance_after = updated_tx['balance_after'] if updated_tx else existing['balance_after']
                    
                    response = format_success_message(transaction)
                    markup = None
                    from services.cafeteria_service import is_cafeteria_payment
                    from bot.keyboards import get_cafeteria_selection_keyboard
                    if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                        markup = get_cafeteria_selection_keyboard(tx_id, transaction.amount)
                        response += "\n\n🍽️ <b>Cafeteria Bill Detected!</b> Select your menu item below:"

                    response = f"🔄 <b>Updated Existing Transaction #{tx_id} with New Receipt!</b>\n\n" + response
                    await deliver_response(status_msg, message, response, reply_markup=markup, parse_mode='HTML')
                    try:
                        from services.backup_service import backup_to_telegram, export_database_to_json
                        export_database_to_json()
                        asyncio.create_task(backup_to_telegram(context.bot))
                    except Exception:
                        pass
                    return

                date_str = format_display_date(existing['transaction_date'])
                person = existing['person_name'] or "Unknown"
                amt_str = format_currency(existing['amount'])
                notice = ""
                dup_markup = None
                if abs(float(existing['amount']) - float(transaction.amount)) > 0.01:
                    notice = f"\n\n⚠️ <b>Note:</b> Receipt shows <b>{html.escape(format_currency(transaction.amount))}</b>, but existing record has <b>{html.escape(amt_str)}</b>."
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    dup_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton(f"🔄 Correct to {format_currency(transaction.amount)}", callback_data=f"correct_amount:{existing['id']}:{transaction.amount}")],
                        [InlineKeyboardButton("🗑️ Delete Old Record", callback_data=f"delete_confirm:{existing['id']}")]
                    ])
                
                dup_text = (
                    "ℹ️ <b>Transaction Already Recorded</b>\n\n"
                    f"• <b>Type:</b> {html.escape(str(existing['transaction_type']))}\n"
                    f"• <b>Person:</b> {html.escape(str(person))}\n"
                    f"• <b>Amount:</b> {html.escape(amt_str)}\n"
                    f"• <b>Date:</b> {html.escape(date_str)} {html.escape(str(existing['transaction_time'] or ''))}\n"
                    f"• <b>Reference:</b> <code>{html.escape(str(existing['reference_number']))}</code>\n"
                    f"• <b>Balance After:</b> {html.escape(format_currency(existing['balance_after']))}{notice}\n\n"
                    f"💡 To update or edit this transaction, send <code>/edit #{existing['id']}</code>."
                )
                await deliver_response(status_msg, message, dup_text, reply_markup=dup_markup, parse_mode='HTML')
                return


        # Confidence check
        if confidence >= 80:
            # High confidence, save automatically
            from services.cafeteria_service import is_cafeteria_payment
            from bot.keyboards import get_cafeteria_selection_keyboard
            
            # Ensure category is Food & Dining if cafeteria
            if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                transaction.category = "Food & Dining"

            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                markup = None
                if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                    markup = get_cafeteria_selection_keyboard(transaction.id, transaction.amount)
                    response += "\n\n🍽️ <b>Cafeteria Bill Detected!</b> Select your menu item below:"

                await deliver_response(status_msg, message, response, reply_markup=markup, parse_mode='HTML')
                try:
                    asyncio.create_task(backup_to_telegram(context.bot))
                except Exception:
                    pass
                if is_gdrive_available():
                    try:
                        from config import DATA_DIR
                        bkp = DATA_DIR / 'backup_transactions.json'
                        if os.path.exists(bkp):
                            asyncio.create_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)))
                    except Exception:
                        pass
            else:
                await deliver_response(status_msg, message, "⚠️ Transaction already recorded.")
        elif confidence >= 40:
            # Medium confidence, ask for confirmation
            tx_id = uuid.uuid4().hex
            pending_transactions[tx_id] = transaction
            
            date_display = transaction.transaction_date.strftime("%d %b %Y") if hasattr(transaction.transaction_date, 'strftime') else (str(transaction.transaction_date) if transaction.transaction_date else "Today")
            confirm_text = (
                "⚠️ <b>Please confirm this transaction</b>\n\n"
                f"• <b>Type:</b> {html.escape(str(transaction.transaction_type))}\n"
                f"• <b>Amount:</b> {html.escape(format_currency(transaction.amount))}\n"
                f"• <b>Person:</b> {html.escape(str(transaction.person_name or 'Unknown'))}\n"
                f"• <b>Date:</b> {html.escape(str(date_display))}\n"
                f"• <b>Reference:</b> <code>{html.escape(str(transaction.reference_number or 'N/A'))}</code>\n\n"
                "Save this transaction?"
            )
            keyboard = get_confirmation_keyboard(tx_id)
            await deliver_response(status_msg, message, confirm_text, reply_markup=keyboard, parse_mode='HTML')
        else:
            await deliver_response(status_msg, message, "❌ Confidence too low to process automatically. Please check the image.")

    except Exception as e:
        logger.error(f"Error handling image: {e}", exc_info=True)
        await deliver_response(status_msg, message, f"❌ Error processing image: {e}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button presses from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split(":") if ":" in data else [data]
    action = parts[0]
    
    # 1. OCR Confirmation / Cancellation
    if action in ("confirm_tx", "cancel_tx"):
        tx_id = parts[1]
        transaction = pending_transactions.get(tx_id)
        
        if not transaction:
            await query.edit_message_text("❌ Transaction expired or no longer available.")
            return
            
        if action == "confirm_tx":
            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                await query.edit_message_text(response, parse_mode='HTML')
                try:
                    asyncio.create_task(backup_to_telegram(context.bot))
                except Exception:
                    pass
                if is_gdrive_available():
                    try:
                        from config import DATA_DIR
                        bkp = DATA_DIR / 'backup_transactions.json'
                        if os.path.exists(bkp):
                            asyncio.create_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)))
                    except Exception:
                        pass
            else:
                await query.edit_message_text("⚠️ Transaction already recorded.")
            del pending_transactions[tx_id]
            
        elif action == "cancel_tx":
            await query.edit_message_text("❌ Transaction cancelled.")
            del pending_transactions[tx_id]
            
    # 2. Transaction Selection for Edit / Delete
    elif action == "select_edit":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        date_str = tx['transaction_date'] or "Today"
        person = tx['person_name'] or "Unknown"
        text = (
            f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n"
            f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
            f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
            f"• <b>Person:</b> {html.escape(str(person))}\n"
            f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
            "Select what you would like to edit:"
        )
        await query.edit_message_text(text, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='HTML')
        
    elif action == "select_delete":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        date_str = tx['transaction_date'] or "Today"
        person = tx['person_name'] or "Unknown"
        text = (
            f"🗑️ <b>Delete Transaction #{tx_id}</b>\n\n"
            f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
            f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
            f"• <b>Person:</b> {html.escape(str(person))}\n"
            f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
            "Are you sure you want to delete this transaction?"
        )
        await query.edit_message_text(text, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='HTML')

    # 3. Field Selection for Editing
    elif action == "edit_field":
        field = parts[1]
        tx_id = int(parts[2])
        
        context.user_data['action'] = 'waiting_edit_value'
        context.user_data['edit_tx_id'] = tx_id
        context.user_data['edit_field'] = field
        
        prompts = {
            'amount': "💵 Please enter the <b>new amount</b> (e.g. <code>500</code> or <code>1250.50</code>):",
            'person': "👤 Please enter the <b>new name</b> (e.g. <code>Ramesh</code>):",
            'type': "🔄 Please enter the <b>new type</b> (<code>SENT</code> or <code>RECEIVED</code>):",
            'date': "📅 Please enter the <b>new date</b> (e.g. <code>05/09/2026</code> or <code>yesterday</code>):",
            'ref': "🔢 Please enter the <b>new reference/UTR number</b>:"
        }
        prompt = prompts.get(field, "Please enter the new value:")
        await query.edit_message_text(
            f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n{prompt}",
            parse_mode='HTML'
        )
        
    elif action == "edit_cancel":
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)
        await query.edit_message_text("❌ Editing cancelled.")

    # 4. Delete Confirmation
    elif action == "delete_confirm":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return

        amt_str = format_currency(tx['amount'])
        person = tx['person_name'] or "Unknown"

        success = delete_transaction(tx_id)
        if success:
            resequence_transaction_ids()
            new_bal = recalculate_all_balances()
            try:
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            if is_gdrive_available():
                try:
                    from config import DATA_DIR
                    bkp = DATA_DIR / 'backup_transactions.json'
                    if os.path.exists(bkp):
                        asyncio.create_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)))
                except Exception:
                    pass
            await query.edit_message_text(
                f"🗑️ <b>Transaction #{tx_id} Deleted</b>\n\n"
                f"• <b>Amount:</b> {html.escape(amt_str)}\n"
                f"• <b>Person:</b> {html.escape(str(person))}\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Failed to delete transaction.")

    elif action == "delete_cancel":
        context.user_data.pop('action', None)
        await query.edit_message_text("❌ Deletion cancelled.")

    # 4b. Correct Amount for Existing Transaction
    elif action == "correct_amount":
        tx_id = int(parts[1])
        new_amt = float(parts[2])
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        success = update_transaction(tx_id, {'amount': new_amt})
        if success:
            new_bal = recalculate_all_balances()
            try:
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            if is_gdrive_available():
                try:
                    from config import DATA_DIR
                    bkp = DATA_DIR / 'backup_transactions.json'
                    if os.path.exists(bkp):
                        asyncio.create_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)))
                except Exception:
                    pass
            await query.edit_message_text(
                f"✅ <b>Transaction updated successfully!</b>\n\n"
                f"• <b>Amount corrected to:</b> <b>{html.escape(format_currency(new_amt))}</b>\n"
                f"💰 <b>Updated Current Balance:</b> <code>{html.escape(format_currency(new_bal))}</code>",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Failed to update transaction.")

    # 4c. Export Format Selection (PDF / Excel)
    elif action == "export_file":
        fmt = parts[1]
        if fmt == "pdf":
            from bot.commands import send_pdf_report
            await query.edit_message_text("⏳ Generating official PDF statement...")
            await send_pdf_report(query.message.chat, context.bot)
        elif fmt == "excel":
            from bot.commands import send_excel_report
            await query.edit_message_text("⏳ Generating Excel spreadsheet...")
            await send_excel_report(query.message.chat, context.bot)

    # 5. Interactive Filter Callbacks
    elif action == "filter":
        filter_type = parts[1]
        now = get_current_time_in_tz()
        
        if filter_type == "today":
            txs = search_transactions(target_date=now.date(), sort_by="date_desc")
            title = f"📅 Today's Transactions ({now.strftime('%d %b %Y')})"
        elif filter_type == "yesterday":
            y_date = now.date() - timedelta(days=1)
            txs = search_transactions(target_date=y_date, sort_by="date_desc")
            title = f"📅 Yesterday's Transactions ({y_date.strftime('%d %b %Y')})"
        elif filter_type == "this_month":
            txs = search_transactions(year=now.year, month=now.month, sort_by="date_desc")
            title = f"🗓️ This Month's Transactions ({now.strftime('%B %Y')})"
        elif filter_type == "monthly_stats":
            stats = get_monthly_summary(now.year, now.month)
            top_p = f"{stats['top_recipient']['person_name']} ({format_currency(stats['top_recipient']['total'])})" if stats['top_recipient'] else "N/A"
            text = (
                f"📊 <b>Monthly Analytics - {now.strftime('%B %Y')}</b>\n\n"
                f"🔴 <b>Total Sent:</b> {html.escape(format_currency(stats['total_sent']))}\n"
                f"🟢 <b>Total Received:</b> {html.escape(format_currency(stats['total_received']))}\n"
                f"📈 <b>Net Savings:</b> {html.escape(format_currency(stats['net_savings']))}\n\n"
                f"🔢 <b>Total Transactions:</b> {stats['tx_count']}\n"
                f"🏆 <b>Top Recipient:</b> {html.escape(str(top_p))}"
            )
            await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
        elif filter_type == "type_sent":
            txs = search_transactions(tx_type="SENT", limit=15, sort_by="date_desc")
            title = "🔴 Recent Sent Transactions"
        elif filter_type == "type_received":
            txs = search_transactions(tx_type="RECEIVED", limit=15, sort_by="date_desc")
            title = "🟢 Recent Received Transactions"
        elif filter_type == "show_ids":
            txs = get_recent_transactions(limit=10)
            if not txs:
                await query.edit_message_text("No transactions found.")
                return
            text = "🔍 <b>Transactions (With IDs & Ref)</b>\n\n"
            for t in txs:
                text += (
                    f"🆔 <b>ID: #{t['id']}</b> | {html.escape(str(t['transaction_type']))}\n"
                    f"📅 {html.escape(str(t['transaction_date']))} | 👤 {html.escape(str(t['person_name'] or 'N/A'))}\n"
                    f"💵 {html.escape(format_currency(t['amount']))} | 🔢 Ref: <code>{html.escape(str(t['reference_number'] or 'N/A'))}</code>\n\n"
                )
            await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
        elif filter_type == "open_sort":
            await query.edit_message_text("🔀 <b>Choose Sorting Order:</b>", reply_markup=get_sort_keyboard(), parse_mode='HTML')
            return
        else:
            txs = []
            title = "Filtered Transactions"
            
        if not txs:
            await query.edit_message_text(f"No transactions found for <b>{html.escape(title)}</b>.", reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
            
        total_amt = sum(t['amount'] for t in txs)
        text = f"📜 <b>{html.escape(title)}</b> (Total: {html.escape(format_currency(total_amt))})\n\n"
        for t in txs[:10]:
            person = t['person_name'] or "Unknown"
            date_s = format_display_date(t['transaction_date'])
            time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += f"• <b>{html.escape(date_s)}</b>{html.escape(time_str)} | {type_badge}\n👤 {html.escape(str(person))} | 💵 {html.escape(format_currency(t['amount']))}\n\n"
        await query.edit_message_text(text, reply_markup=get_filter_keyboard(), parse_mode='HTML')

    # 6. Sorting Callbacks
    elif action == "sort":
        sort_by = parts[1]
        if sort_by == "back_filters":
            await query.edit_message_text("🎛️ <b>Filter & Sort Transactions:</b>\n\nChoose an option below:", reply_markup=get_filter_keyboard(), parse_mode='HTML')
            return
            
        sort_names = {
            "date_desc": "Date (Newest first)",
            "date_asc": "Date (Oldest first)",
            "amount_desc": "Amount (Highest first)",
            "amount_asc": "Amount (Lowest first)"
        }
        txs = search_transactions(sort_by=sort_by, limit=10)
        if not txs:
            await query.edit_message_text("No transactions found.")
            return
            
        text = f"🔀 <b>Sorted by: {html.escape(sort_names.get(sort_by, sort_by))}</b>\n\n"
        for t in txs:
            date_s = format_display_date(t['transaction_date'])
            time_str = f" | ⏰ {t['transaction_time']}" if t['transaction_time'] and t['transaction_time'] != 'Unknown Time' else ""
            person = t['person_name'] or "Unknown"
            type_badge = "🔴 SENT" if t['transaction_type'] == 'SENT' else "🟢 RECEIVED"
            text += (
                f"• <b>{html.escape(date_s)}</b>{html.escape(time_str)} | {type_badge}\n"
                f"👤 {html.escape(str(person))}\n"
                f"💵 {html.escape(format_currency(t['amount']))}\n"
                f"Balance: {html.escape(format_currency(t['balance_after']))}\n\n"
            )
        await query.edit_message_text(text, reply_markup=get_sort_keyboard(), parse_mode='HTML')

    # 7. Cafeteria Menu Callbacks
    elif action == "cafe_pick":
        tx_id = int(parts[1])
        item_name = parts[2]
        tx = get_transaction_by_id(tx_id)
        if tx:
            new_name = f"VIKRAMAN NAIR K (Cafeteria: {item_name})"
            update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'})
            try:
                from services.backup_service import backup_to_telegram
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            amt_s = format_currency(tx['amount'])
            bal_s = format_currency(tx['balance_after'])
            from bot.keyboards import get_cafeteria_tagged_keyboard
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

    elif action == "cafe_mode":
        tx_id = int(parts[1])
        mode = parts[2]
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        
        if mode == "1":
            from bot.keyboards import get_cafeteria_single_item_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Single Item Selection (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Pick the single item you ordered below or enter custom amount:",
                reply_markup=get_cafeteria_single_item_keyboard(tx_id, amt),
                parse_mode='HTML'
            )
        elif mode == "2":
            from bot.keyboards import get_cafeteria_two_items_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Two Items Mode (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Pick your combination below or open the plate builder:",
                reply_markup=get_cafeteria_two_items_keyboard(tx_id, amt),
                parse_mode='HTML'
            )
        elif mode == "cart":
            from bot.keyboards import get_cafeteria_cart_keyboard
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
            from bot.keyboards import get_cafeteria_selection_keyboard
            await query.edit_message_text(
                f"📋 <b>Browse Cafeteria Menu (Paid {html.escape(format_currency(amt))}):</b>\n\n"
                f"Select a category below to see all items:",
                reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
                parse_mode='HTML'
            )

    elif action == "cafe_custom_prompt":
        tx_id = int(parts[1])
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

    elif action == "cafe_cart_add":
        tx_id = int(parts[1])
        item_name = parts[2]
        item_price = float(parts[3])
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        
        cart_key = f'cafe_cart_{tx_id}'
        if cart_key not in context.user_data:
            context.user_data[cart_key] = []
        context.user_data[cart_key].append({'name': item_name, 'price': item_price})
        
        cart = context.user_data[cart_key]
        total_cart = sum(it['price'] for it in cart)
        cart_lines = "\n".join([f"• {it['name']} — ₹{it['price']:.0f}" for it in cart])
        
        from bot.keyboards import get_cafeteria_cart_keyboard
        await query.edit_message_text(
            f"🛒 <b>CAFETERIA PLATE BUILDER</b>\n"
            f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
            f"<b>Items in Plate ({len(cart)}):</b>\n{cart_lines}\n\n"
            f"<b>Plate Sum:</b> <b>{html.escape(format_currency(total_cart))}</b> / {html.escape(format_currency(amt))}\n\n"
            f"Tap more items or tap <b>Save Plate</b> when done:",
            reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, cart),
            parse_mode='HTML'
        )

    elif action == "cafe_cart_clear":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        context.user_data[f'cafe_cart_{tx_id}'] = []
        from bot.keyboards import get_cafeteria_cart_keyboard
        await query.edit_message_text(
            f"🛒 <b>Plate Cleared!</b>\n"
            f"Paid Bill: <b>{html.escape(format_currency(amt))}</b>\n\n"
            f"Tap items below to build your plate:",
            reply_markup=get_cafeteria_cart_keyboard(tx_id, amt, []),
            parse_mode='HTML'
        )

    elif action == "cafe_cart_done":
        tx_id = int(parts[1])
        cart = context.user_data.pop(f'cafe_cart_{tx_id}', [])
        tx = get_transaction_by_id(tx_id)
        if tx and cart:
            item_names = " + ".join([it['name'] for it in cart])
            new_name = f"VIKRAMAN NAIR K (Cafeteria: {item_names})"
            update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'})
            try:
                from services.backup_service import backup_to_telegram
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            amt_s = format_currency(tx['amount'])
            bal_s = format_currency(tx['balance_after'])
            from bot.keyboards import get_cafeteria_tagged_keyboard
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
            from bot.keyboards import get_cafeteria_selection_keyboard
            await query.edit_message_text(
                "⚠️ No items were in the plate. Choose an item below:",
                reply_markup=get_cafeteria_selection_keyboard(tx_id, float(tx['amount'])),
                parse_mode='HTML'
            )

    elif action == "cafe_cat":
        tx_id = int(parts[1])
        cat_name = parts[2]
        from bot.keyboards import get_cafeteria_category_keyboard
        tx = get_transaction_by_id(tx_id)
        amt_str = format_currency(tx['amount']) if tx else ""
        await query.edit_message_text(
            f"🍽️ <b>Cafeteria Menu — {html.escape(cat_name)}</b>\n"
            f"Bill Amount: <b>{html.escape(amt_str)}</b>\n\n"
            f"Tap any item to tag it:",
            reply_markup=get_cafeteria_category_keyboard(tx_id, cat_name),
            parse_mode='HTML'
        )

    elif action == "cafe_back":
        tx_id = int(parts[1])
        from bot.keyboards import get_cafeteria_selection_keyboard
        tx = get_transaction_by_id(tx_id)
        amt = float(tx['amount']) if tx else 0.0
        await query.edit_message_text(
            f"🍽️ <b>Cafeteria Menu Selection (Paid {html.escape(format_currency(amt))}):</b>\n\n"
            f"<b>How many items or what did you order?</b>\n"
            f"Select an option below to tag your order:",
            reply_markup=get_cafeteria_selection_keyboard(tx_id, amt),
            parse_mode='HTML'
        )

    elif action == "cafe_addon":
        tx_id = int(parts[1])
        addon = parts[2]
        tx = get_transaction_by_id(tx_id)
        if tx:
            current_name = tx['person_name'] or "VIKRAMAN NAIR K (Cafeteria)"
            updated_name = f"{current_name} + {addon}"
            update_transaction(tx_id, {'person_name': updated_name, 'category': 'Food & Dining'})
            from bot.keyboards import get_cafeteria_tagged_keyboard
            await query.edit_message_text(
                f"🍽️ <b>Add-on Tagged:</b> +₹5 {html.escape(addon)}\n"
                f"• Updated Merchant: {html.escape(updated_name)}\n"
                f"• Total Amount: <b>{html.escape(format_currency(tx['amount']))}</b>\n\n"
                f"✅ Updated record!",
                reply_markup=get_cafeteria_tagged_keyboard(tx_id),
                parse_mode='HTML'
            )

    elif action == "cafe_edit":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        if tx:
            from bot.keyboards import get_cafeteria_selection_keyboard
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

    elif action == "cafe_stats":
        from services.cafeteria_service import format_cafeteria_stats
        stats_text = format_cafeteria_stats()
        await query.message.reply_text(stats_text, parse_mode='HTML')

    elif action == "cafe_view_menu":
        from services.cafeteria_service import format_full_menu
        from bot.keyboards import get_menu_view_keyboard
        menu_text = format_full_menu()
        await query.message.reply_text(menu_text, reply_markup=get_menu_view_keyboard(), parse_mode='HTML')

    elif action == "cafe_menu_add_prompt":
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

    elif action == "cafe_menu_del_prompt":
        from database.db import get_custom_menu_items
        custom_items = get_custom_menu_items()
        if not custom_items:
            await query.message.reply_text("ℹ️ No custom menu items found to delete. Predefined standard items cannot be removed.", parse_mode='HTML')
        else:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = []
            for it in custom_items:
                keyboard.append([InlineKeyboardButton(f"🗑️ Delete {it['name']} (₹{it['price']:.0f})", callback_data=f"cafe_del_item:{it['id']}")])
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cafe_del_cancel")])
            await query.message.reply_text(
                "🗑️ <b>Select Custom Menu Item to Remove:</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )

    elif action == "cafe_del_item":
        item_id = int(parts[1])
        from database.db import delete_custom_menu_item_by_id
        success, name = delete_custom_menu_item_by_id(item_id)
        if success:
            await query.edit_message_text(f"✅ Removed custom item: <b>{html.escape(name)}</b> from cafeteria menu.", parse_mode='HTML')
        else:
            await query.edit_message_text("❌ Failed to remove menu item.", parse_mode='HTML')

    elif action == "cafe_del_cancel":
        await query.edit_message_text("❌ Menu item deletion cancelled.")

    elif action == "cafe_edit_last":
        from database.queries import get_cafeteria_transactions
        from bot.keyboards import get_cafeteria_selection_keyboard
        cafe_txs = get_cafeteria_transactions(limit=1)
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

    elif action == "cafe_skip":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id)
        amt_s = format_currency(tx['amount']) if tx else ""
        from bot.keyboards import get_cafeteria_tagged_keyboard
        await query.edit_message_text(
            f"✅ <b>Cafeteria Payment Recorded</b> ({html.escape(amt_s)})\n"
            f"Tagged as: 🍔 <b>Food & Dining</b> (General)",
            reply_markup=get_cafeteria_tagged_keyboard(tx_id),
            parse_mode='HTML'
        )




def format_success_message(t) -> str:
    """Formats transaction confirmation message in clean, robust HTML with category badge & budget alerts."""
    icon = "🔴 Payment Sent" if t.transaction_type == 'SENT' else "🟢 Payment Received"
    person_label = "To" if t.transaction_type == 'SENT' else "From"
    person_name = html.escape(str(t.person_name or "Unknown"))

    if hasattr(t.transaction_date, 'strftime'):
        date_str = t.transaction_date.strftime("%d %b %Y")
    elif t.transaction_date:
        date_str = str(t.transaction_date)
    else:
        date_str = "Today"
    date_str = html.escape(date_str)

    time_part = f" ({html.escape(t.transaction_time)})" if t.transaction_time and t.transaction_time not in ('N/A', 'Unknown Time') else ""
    bank_part = f"\n🏦 <b>Bank:</b> {html.escape(t.bank_name)}" if t.bank_name else ""
    ref_part = f"\n🔢 <b>Ref / UTR:</b> <code>{html.escape(str(t.reference_number))}</code>" if t.reference_number else ""
    app_part = f" • <i>{html.escape(t.payment_app)}</i>" if t.payment_app and t.payment_app not in ('Generic', '') else ""

    # Category styling
    from services.category_service import get_category_icon
    cat_val = getattr(t, 'category', 'General') or 'General'
    cat_icon = get_category_icon(cat_val)
    cat_part = f"\n🏷️ <b>Category:</b> {cat_icon} {html.escape(cat_val)}"

    bal_before = html.escape(format_currency(t.balance_before))
    bal_after = html.escape(format_currency(t.balance_after))
    amt = html.escape(format_currency(t.amount))

    # Proactive budget alert check
    from services.budget_service import check_budget_alert
    budget_alert = check_budget_alert(t.amount, t.transaction_type)

    return (
        f"✅ <b>{icon}</b>\n\n"
        f"👤 <b>{person_label}:</b> {person_name}\n"
        f"💵 <b>Amount:</b> <b>{amt}</b>{app_part}\n"
        f"📅 <b>Date:</b> {date_str}{time_part}"
        f"{cat_part}"
        f"{bank_part}"
        f"{ref_part}\n\n"
        f"💰 <b>Balance:</b> {bal_before} ➔ <b>{bal_after}</b>"
        f"{budget_alert}"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming text messages, interactive steps, and payment parsing."""
    if not await is_authorized(update): return
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    message_id = str(update.message.message_id)
    
    # Normalize commands e.g. \\date, /search, /monthly, /filter, /sort, /details
    from bot.commands import (
        edit_command, delete_command, history_command, balance_command,
        date_command, search_command, monthly_command, filter_command, sort_command, details_command, amount_command
    )
    cmd_lower = text.lower()
    
    if cmd_lower in (r'\\edit', 'edit', '/edit'):
        await edit_command(update, context)
        return
    elif cmd_lower in (r'\\delete', 'delete', '/delete'):
        await delete_command(update, context)
        return
    elif cmd_lower in (r'\\history', 'history', '/history'):
        await history_command(update, context)
        return
    elif cmd_lower in (r'\\balance', 'balance', '/balance'):
        await balance_command(update, context)
        return
    elif cmd_lower.startswith((r'\\date', 'date ', '/date ')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await date_command(update, context)
        return
    elif cmd_lower.startswith((r'\\search', 'search ', '/search ')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await search_command(update, context)
        return
    elif cmd_lower.startswith((r'\\amount', 'amount ', '/amount ')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await amount_command(update, context)
        return
    elif cmd_lower in (r'\\monthly', 'monthly', '/monthly', 'stats', '/stats'):
        await monthly_command(update, context)
        return
    elif cmd_lower in (r'\\filter', 'filter', '/filter'):
        await filter_command(update, context)
        return
    elif cmd_lower in (r'\\sort', 'sort', '/sort'):
        await sort_command(update, context)
        return
    elif cmd_lower in (r'\\details', 'details', '/details', 'ids', '/ids'):
        await details_command(update, context)
        return
    elif cmd_lower in (r'\\menu', 'menu', '/menu', 'cafeteria', '/cafeteria', 'canteen', '/canteen'):
        from bot.commands import menu_command
        await menu_command(update, context)
        return
    elif cmd_lower in (r'\\cafestats', 'cafestats', '/cafestats', 'cafespends', '/cafespends'):
        from bot.commands import cafestats_command
        await cafestats_command(update, context)
        return
    elif cmd_lower.startswith((r'\\cafeedit', 'cafeedit', '/cafeedit', 'editcafe', '/editcafe')):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        from bot.commands import cafeedit_command
        await cafeedit_command(update, context)
        return
        
    # Quick standalone amount search: if user just sends a number like "5000" or "400"
    clean_num = text.replace(',', '').replace('₹', '').strip()
    if clean_num.isdigit() and len(clean_num) >= 2:
        val = float(clean_num)
        if 'action' not in context.user_data:
            txs = search_transactions(amount=val)
            if txs:
                context.args = [clean_num]
                await amount_command(update, context)
                return

    # Check if user is in an interactive state
    pending_action = context.user_data.get('action')
    
    if pending_action == 'waiting_edit_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            recent_ids = context.user_data.get('recent_edit_ids') or [t['id'] for t in get_recent_transactions(limit=10)]
            tx = None
            if 1 <= num <= len(recent_ids):
                tx = get_transaction_by_id(recent_ids[num - 1])
            if not tx:
                tx = get_transaction_by_id(num)
            if tx:
                tx_id = tx['id']
                context.user_data.pop('action', None)
                context.user_data.pop('recent_edit_ids', None)
                date_str = tx['transaction_date'] or "Today"
                person = tx['person_name'] or "Unknown"
                msg = (
                    f"✏️ <b>Editing Transaction #{tx_id}</b>\n\n"
                    f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
                    f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
                    f"• <b>Person:</b> {html.escape(str(person))}\n"
                    f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
                    "Select what you would like to edit:"
                )
                await update.message.reply_text(msg, reply_markup=get_edit_fields_keyboard(tx_id), parse_mode='HTML')
                return
            else:
                await update.message.reply_text("❌ Transaction not found. Please send a valid number (1-6) or ID:")
                return
                
    elif pending_action == 'waiting_delete_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            recent_ids = context.user_data.get('recent_delete_ids') or [t['id'] for t in get_recent_transactions(limit=10)]
            tx = None
            if 1 <= num <= len(recent_ids):
                tx = get_transaction_by_id(recent_ids[num - 1])
            if not tx:
                tx = get_transaction_by_id(num)
            if tx:
                tx_id = tx['id']
                context.user_data.pop('action', None)
                context.user_data.pop('recent_delete_ids', None)
                date_str = tx['transaction_date'] or "Today"
                person = tx['person_name'] or "Unknown"
                msg = (
                    f"🗑️ <b>Delete Transaction #{tx_id}</b>\n\n"
                    f"• <b>Type:</b> {html.escape(str(tx['transaction_type']))}\n"
                    f"• <b>Amount:</b> {html.escape(format_currency(tx['amount']))}\n"
                    f"• <b>Person:</b> {html.escape(str(person))}\n"
                    f"• <b>Date:</b> {html.escape(str(date_str))}\n\n"
                    "Are you sure you want to delete this transaction?"
                )
                await update.message.reply_text(msg, reply_markup=get_delete_confirm_keyboard(tx_id), parse_mode='HTML')
                return
            else:
                await update.message.reply_text("❌ Transaction not found. Please send a valid number (1-6) or ID:")
                return
                
    elif pending_action == 'waiting_edit_value':
        tx_id = context.user_data.get('edit_tx_id')
        field = context.user_data.get('edit_field')

        updates = {}
        needs_recalc = False

        if field == 'amount':
            new_amt = parse_amount(text)
            if new_amt <= 0:
                await update.message.reply_text("❌ Invalid amount. Please enter a valid positive number:")
                return
            updates['amount'] = new_amt
            needs_recalc = True
        elif field == 'person':
            updates['person_name'] = text.title()
            tx = get_transaction_by_id(tx_id)
            if tx and tx['transaction_type'] == 'SENT':
                updates['recipient_name'] = text.title()
            else:
                updates['sender_name'] = text.title()
        elif field == 'type':
            new_type = text.upper()
            if new_type not in ('SENT', 'RECEIVED'):
                await update.message.reply_text("❌ Please enter <code>SENT</code> or <code>RECEIVED</code>:", parse_mode='HTML')
                return
            updates['transaction_type'] = new_type
            needs_recalc = True
        elif field == 'date':
            parsed_d = parse_date(text)
            if not parsed_d:
                await update.message.reply_text("❌ Invalid date. Example: <code>05/09/2026</code> or <code>yesterday</code>:", parse_mode='HTML')
                return
            updates['transaction_date'] = parsed_d
            needs_recalc = True
        elif field == 'ref':
            updates['reference_number'] = text

        success = update_transaction(tx_id, updates)
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)

        if success:
            if needs_recalc:
                new_bal = recalculate_all_balances()
            else:
                new_bal = get_balance_setting()

            updated_tx = get_transaction_by_id(tx_id)
            person = (updated_tx['person_name'] if updated_tx else '') or "Unknown"
            amt_s = format_currency(updated_tx['amount']) if updated_tx else ''
            bal_flow = ""
            if updated_tx and 'balance_before' in updated_tx and 'balance_after' in updated_tx:
                bal_flow = f"\n💰 <b>Balance Flow:</b> {html.escape(format_currency(updated_tx['balance_before']))} ➔ <b>{html.escape(format_currency(updated_tx['balance_after']))}</b>"

            try:
                from services.backup_service import backup_to_telegram
                asyncio.create_task(backup_to_telegram(context.bot))
            except Exception:
                pass
            if is_gdrive_available():
                try:
                    from config import DATA_DIR
                    bkp = DATA_DIR / 'backup_transactions.json'
                    if os.path.exists(bkp):
                        asyncio.create_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)))
                except Exception:
                    pass
            await update.message.reply_text(
                f"✅ <b>Transaction #{tx_id} Updated</b>\n\n"
                f"👤 <b>Person:</b> {html.escape(str(person))}\n"
                f"💵 <b>Amount:</b> <b>{html.escape(amt_s)}</b>{bal_flow}\n\n"
                f"💳 <b>Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>",
                parse_mode='HTML'
            )
            return
        else:
            await update.message.reply_text("❌ Failed to update transaction.")
            return

    elif pending_action == 'waiting_cafe_custom_amount':
        tx_id = context.user_data.pop('cafe_tx_id', None)
        item_type = context.user_data.pop('cafe_item', 'Custom')
        context.user_data.pop('action', None)
        
        parsed_amt = parse_amount(text)
        custom_note = text.strip()
        
        if tx_id:
            tx = get_transaction_by_id(tx_id)
            if tx:
                if parsed_amt > 0:
                    tag_desc = f"{item_type} (₹{parsed_amt:.0f})" if item_type != 'Custom' else f"Custom Item (₹{parsed_amt:.0f})"
                else:
                    tag_desc = f"{item_type}: {custom_note}"
                    
                new_name = f"VIKRAMAN NAIR K (Cafeteria: {tag_desc})"
                update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'})
                try:
                    from services.backup_service import backup_to_telegram
                    asyncio.create_task(backup_to_telegram(context.bot))
                except Exception:
                    pass
                amt_s = format_currency(tx['amount'])
                bal_s = format_currency(tx['balance_after'])
                from bot.keyboards import get_cafeteria_tagged_keyboard
                await update.message.reply_text(
                    f"🍽️ <b>Cafeteria Order Tagged!</b>\n\n"
                    f"• <b>Custom Item:</b> 🍽️ <b>{html.escape(tag_desc)}</b>\n"
                    f"• <b>Merchant:</b> VIKRAMAN NAIR K\n"
                    f"• <b>Bill Amount:</b> <b>{html.escape(amt_s)}</b>\n"
                    f"• <b>Category:</b> 🍔 Food & Dining\n"
                    f"• <b>Balance:</b> {html.escape(bal_s)}\n\n"
                    f"✅ Successfully updated your cafeteria record!",
                    reply_markup=get_cafeteria_tagged_keyboard(tx_id),
                    parse_mode='HTML'
                )
                return
        await update.message.reply_text(f"🍽️ Tagged cafeteria order: <b>{html.escape(text)}</b>", parse_mode='HTML')
        return

    elif pending_action == 'waiting_add_menu_item':
        context.user_data.pop('action', None)
        from services.cafeteria_service import add_custom_menu_item
        full_arg = text.strip()
        name, price, category = None, None, "Custom"
        if "," in full_arg:
            parts = [p.strip() for p in full_arg.split(",")]
            name = parts[0]
            if len(parts) > 1:
                try:
                    price = float(parts[1])
                except ValueError:
                    price = None
            if len(parts) > 2:
                category = parts[2]
        else:
            tokens = full_arg.split()
            price_idx = -1
            for idx, tok in enumerate(tokens):
                try:
                    p_val = float(tok)
                    if p_val > 0:
                        price = p_val
                        price_idx = idx
                        break
                except ValueError:
                    continue
            if price_idx != -1:
                name = " ".join(tokens[:price_idx])
                cat_tokens = tokens[price_idx+1:]
                category = " ".join(cat_tokens) if cat_tokens else "Custom"
            else:
                name = full_arg

        if not name or price is None or price <= 0:
            await update.message.reply_text(
                "❌ <b>Invalid format!</b>\n\n"
                "Please send: <code>Item Name, Price, Category</code>\n"
                "Example: <code>Paneer Roll, 45, Snacks</code>",
                parse_mode='HTML'
            )
            return

        success, msg = add_custom_menu_item(name, price, category, is_veg=True)
        if success:
            await update.message.reply_text(
                f"✅ <b>Custom Menu Item Added!</b>\n\n"
                f"• <b>Item:</b> 🍽️ <b>{html.escape(name)}</b>\n"
                f"• <b>Price:</b> <b>₹{price:.0f}</b>\n"
                f"• <b>Category:</b> {html.escape(category)}\n"
                f"• <b>Type:</b> 🟢 Pure Veg\n\n"
                f"Use <code>/menu</code> to view updated menu!",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')
        return

    elif pending_action == 'waiting_del_menu_item':
        context.user_data.pop('action', None)
        from services.cafeteria_service import delete_custom_menu_item
        success, msg = delete_custom_menu_item(text.strip())
        if success:
            await update.message.reply_text(f"✅ {html.escape(msg)}", parse_mode='HTML')
        else:
            await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')
        return

    # Try parsing text as a transaction
    try:
        transaction, confidence = process_transaction(text, "", message_id, chat_id)
        if transaction.amount and transaction.amount > 0 and transaction.transaction_type:
            if confidence >= 80:
                from services.cafeteria_service import is_cafeteria_payment
                from bot.keyboards import get_cafeteria_selection_keyboard
                if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                    transaction.category = "Food & Dining"

                success = commit_transaction(transaction)
                if success:
                    response = format_success_message(transaction)
                    markup = None
                    if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                        markup = get_cafeteria_selection_keyboard(transaction.id, transaction.amount)
                        response += "\n\n🍽️ <b>Cafeteria Bill Detected!</b> Select your menu item below:"

                    await update.message.reply_text(response, reply_markup=markup, parse_mode='HTML')
                    try:
                        asyncio.create_task(backup_to_telegram(context.bot))
                    except Exception:
                        pass
                else:
                    await update.message.reply_text("⚠️ Transaction already recorded.")
                return
            elif confidence >= 30:
                tx_id = uuid.uuid4().hex
                pending_transactions[tx_id] = transaction
                date_display = transaction.transaction_date.strftime("%d %b %Y") if hasattr(transaction.transaction_date, 'strftime') else (str(transaction.transaction_date) if transaction.transaction_date else "Today")
                confirm_text = (
                    "⚠️ <b>Please confirm this transaction</b>\n\n"
                    f"• <b>Type:</b> {html.escape(str(transaction.transaction_type))}\n"
                    f"• <b>Amount:</b> {html.escape(format_currency(transaction.amount))}\n"
                    f"• <b>Person:</b> {html.escape(str(transaction.person_name or 'Unknown'))}\n"
                    f"• <b>Date:</b> {html.escape(str(date_display))}\n\n"
                    "Save this transaction?"
                )
                keyboard = get_confirmation_keyboard(tx_id)
                await update.message.reply_text(confirm_text, reply_markup=keyboard, parse_mode='HTML')
                return
    except Exception as e:
        logger.error(f"Error parsing text message: {e}", exc_info=True)
    
    # General help message if no payment detected
    await update.message.reply_text(
        "👋 You can send me:\n"
        "1. A <b>screenshot</b> of your payment receipt.\n"
        "2. A <b>text message</b> describing a payment (e.g. <code>Paid 500 to Ramesh</code> or <code>Received 6200 from Johnson</code>).\n\n"
        "<b>Commands:</b>\n"
        "/balance - View balance\n"
        "/history - View transactions\n"
        "/edit - Edit a transaction\n"
        "/delete - Delete a transaction",
        parse_mode='HTML'
    )
