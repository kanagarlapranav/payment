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

        # Tier 3: Asynchronously archive receipt to Google Drive if available
        if is_gdrive_available():
            try:
                asyncio.create_task(asyncio.to_thread(upload_receipt_to_drive, str(image_path)))
            except Exception as gd_err:
                logger.warning(f"Background Google Drive upload could not be scheduled: {gd_err}")
        else:
            # Clean up local image immediately if not archiving to Drive
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except OSError:
                pass
        
        # Basic validation
        if not transaction.amount or transaction.amount <= 0:
            await deliver_response(status_msg, message, "⚠️ Could not detect a valid amount. Please provide a clearer screenshot or enter manually.")
            return
            
        if not transaction.transaction_type:
            await deliver_response(status_msg, message, f"⚠️ Transaction type (SENT/RECEIVED) could not be determined reliably.\nAmount found: {format_currency(transaction.amount)}")
            return
            
        # Check if already recorded (duplicate prevention with friendly informative response)
        if transaction.reference_number:
            existing = get_transaction_by_reference(transaction.reference_number)
            if existing:
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
            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                await deliver_response(status_msg, message, response, parse_mode='HTML')
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
    if ":" not in data:
        return
        
    parts = data.split(":")
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


def format_success_message(t) -> str:
    """Formats transaction confirmation message in clean, robust HTML."""
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

    bal_before = html.escape(format_currency(t.balance_before))
    bal_after = html.escape(format_currency(t.balance_after))
    amt = html.escape(format_currency(t.amount))

    return (
        f"✅ <b>{icon}</b>\n\n"
        f"👤 <b>{person_label}:</b> {person_name}\n"
        f"💵 <b>Amount:</b> <b>{amt}</b>{app_part}\n"
        f"📅 <b>Date:</b> {date_str}{time_part}"
        f"{bank_part}"
        f"{ref_part}\n\n"
        f"💰 <b>Balance:</b> {bal_before} ➔ <b>{bal_after}</b>"
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

    # Try parsing text as a transaction
    try:
        transaction, confidence = process_transaction(text, "", message_id, chat_id)
        if transaction.amount and transaction.amount > 0 and transaction.transaction_type:
            if confidence >= 80:
                success = commit_transaction(transaction)
                if success:
                    response = format_success_message(transaction)
                    await update.message.reply_text(response, parse_mode='HTML')
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
