from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
import os
import time
import uuid
import json
import asyncio
import html
import re
from datetime import datetime, timedelta, date
import config
from config import TELEGRAM_USER_ID, IMAGE_DIR, logger
from bot.auth import (
    require_authorized, require_admin, require_owner, require_member, is_owner, is_authorized_user,
    get_callback_policy,
    get_workspace_context, resolve_workspace_context, WORKSPACE_CALLBACK_POLICY,
    get_effective_user_id
)
from bot.commands import (
    is_admin_user, render_home_menu_text, render_history_page,
    render_contacts_ledger_text, render_transaction_detail, render_backup_status_text
)
from bot.keyboards import (
    get_edit_fields_keyboard, get_delete_confirm_keyboard,
    get_filter_keyboard, get_sort_keyboard, get_home_menu_keyboard, get_back_to_menu_keyboard,
    get_confirmation_card_keyboard, get_edit_pending_fields_keyboard, get_category_picker_keyboard,
    get_quick_undo_keyboard, get_quick_add_keyboard, get_history_paginated_keyboard,
    get_add_menu_keyboard, get_more_menu_keyboard, get_transaction_detail_keyboard,
    get_backup_status_keyboard, get_json_import_confirm_keyboard, get_delete_confirmed_keyboard
)
from ocr.extractor import perform_ocr, perform_ocr_async
from ocr.gemini_vision import is_gemini_available, extract_transaction_with_gemini
from services.gdrive_service import is_gdrive_available, upload_receipt_to_drive, upload_backup_to_drive
from services.transaction_service import process_transaction, commit_transaction
from services.balance_service import recalculate_all_balances, get_today_summary, get_overall_summary
from services.backup_service import backup_to_telegram
from database.models import Transaction
from database.queries import (
    get_transaction_by_id, get_transaction_by_reference, update_transaction, delete_transaction,
    search_transactions, get_monthly_summary, get_recent_transactions, get_balance_setting,
    get_payee_category, remember_payee_category, find_potential_duplicate, get_top_payees,
    get_daily_spend_series, get_month_comparison_stats, get_transactions_paginated, get_contact_ledger,
    get_category_summary, save_pending_receipt, get_pending_receipt, delete_pending_receipt,
    get_default_workspace_id, can_user_modify_transaction
)
from utils.currency import parse_amount, format_currency, normalize_amount_string
from utils.dates import parse_date, get_current_time_in_tz, format_display_date
from utils.validation import parse_decimal_amount, validate_name
from bot.callbacks import handle_nav_callback, NAV_DISPATCH_TABLE, handle_cafe_callback, CAFE_DISPATCH_TABLE

import time

# In-memory store for pending transactions awaiting confirmation
pending_transactions = {}
_pending_transactions_timestamps = {}

def parse_int_arg(parts: list, index: int) -> int | None:
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return None

def parse_float_arg(parts: list, index: int) -> float | None:
    try:
        return float(parts[index])
    except (IndexError, ValueError):
        return None

def _prune_pending_transactions():
    now = time.time()
    cutoff = now - 86400  # 24 hours TTL
    expired_keys = [k for k, t in _pending_transactions_timestamps.items() if t < cutoff]
    for k in expired_keys:
        _pending_transactions_timestamps.pop(k, None)
        pending_transactions.pop(k, None)
    if len(pending_transactions) > 1000:
        sorted_keys = sorted(_pending_transactions_timestamps.items(), key=lambda x: x[1])
        for k, _ in sorted_keys[:len(pending_transactions) - 1000]:
            _pending_transactions_timestamps.pop(k, None)
            pending_transactions.pop(k, None)

def set_pending_transaction(pending_id: str, transaction, workspace_id: str = None) -> None:
    """Stores pending transaction in memory and persists to SQLite database with workspace scoping."""
    _prune_pending_transactions()
    now = time.time()
    if hasattr(transaction, '__dict__') and not hasattr(transaction, 'created_at'):
        try:
            transaction.created_at = now
        except Exception:
            pass
    pending_transactions[pending_id] = transaction
    _pending_transactions_timestamps[pending_id] = now
    ws_id = workspace_id or getattr(transaction, 'workspace_id', None)
    if ws_id:
        pending_transactions[(ws_id, pending_id)] = transaction
        _pending_transactions_timestamps[(ws_id, pending_id)] = now
    try:
        save_pending_receipt(pending_id, transaction, workspace_id=ws_id)
    except Exception as e:
        logger.error(f"Error persisting pending receipt {pending_id}: {e}")

def fetch_pending_transaction(pending_id: str, workspace_id: str = None):
    """Fetches pending transaction from memory or falls back to SQLite database with workspace scoping."""
    _prune_pending_transactions()
    if workspace_id and (workspace_id, pending_id) in pending_transactions:
        return pending_transactions[(workspace_id, pending_id)]
    
    tx = pending_transactions.get(pending_id)
    if tx:
        tx_ws = getattr(tx, 'workspace_id', None)
        if workspace_id and tx_ws and tx_ws != workspace_id:
            return None
        return tx

    try:
        tx = get_pending_receipt(pending_id, workspace_id=workspace_id)
        if tx:
            tx_ws = getattr(tx, 'workspace_id', None)
            if workspace_id and tx_ws and tx_ws != workspace_id:
                return None
            now = time.time()
            if hasattr(tx, '__dict__') and not hasattr(tx, 'created_at'):
                try:
                    tx.created_at = now
                except Exception:
                    pass
            pending_transactions[pending_id] = tx
            _pending_transactions_timestamps[pending_id] = now
            ws_id = workspace_id or tx_ws
            if ws_id:
                pending_transactions[(ws_id, pending_id)] = tx
                _pending_transactions_timestamps[(ws_id, pending_id)] = now
            return tx
    except Exception as e:
        logger.error(f"Error retrieving pending receipt {pending_id}: {e}")
    return None

def pop_pending_transaction(pending_id: str, workspace_id: str = None):
    """Pops pending transaction from memory and deletes from SQLite database with workspace scoping."""
    _prune_pending_transactions()
    tx = None
    if workspace_id:
        tx = pending_transactions.pop((workspace_id, pending_id), None)
        _pending_transactions_timestamps.pop((workspace_id, pending_id), None)
    
    fallback_tx = pending_transactions.get(pending_id)
    if fallback_tx:
        tx_ws = getattr(fallback_tx, 'workspace_id', None)
        if not workspace_id or not tx_ws or tx_ws == workspace_id:
            pending_transactions.pop(pending_id, None)
            _pending_transactions_timestamps.pop(pending_id, None)
            if not tx:
                tx = fallback_tx

    try:
        db_tx = get_pending_receipt(pending_id, workspace_id=workspace_id)
        delete_pending_receipt(pending_id, workspace_id=workspace_id)
        if not tx:
            tx = db_tx
    except Exception as e:
        logger.error(f"Error deleting pending receipt {pending_id}: {e}")
    return tx

def reconstruct_transaction_from_card(text: str):
    """Fallback parser to reconstruct a Transaction object directly from the receipt card message text."""
    if not text:
        return None
    try:
        clean = re.sub(r'<[^>]+>', '', text)
        
        # 1. Parse Amount, Type, and Person Name
        m_tx = re.search(r'(?:💸\s*)?(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:<b>)?\s*(→|➔|->|to|←|<-|from)\s*(?:</b>)?\s*([^\n\r🏷📅🏦]+)', clean, re.IGNORECASE)
        if not m_tx:
            m_tx = re.search(r'(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)\s*(→|➔|->|to|←|<-|from)\s*([^\n\r🏷📅🏦]+)', clean, re.IGNORECASE)
            
        if not m_tx:
            return None
            
        amt_str, arrow, person = m_tx.groups()
        amount = normalize_amount_string(amt_str)
        if amount <= 0:
            return None
            
        arrow_clean = arrow.strip().lower()
        if arrow_clean in ('←', '<-', 'from'):
            tx_type = "RECEIVED"
        else:
            tx_type = "SENT"
            
        person = person.strip(' *_\t')
        person = re.sub(r'\s*━━━━━━━━━━━━━━.*', '', person)
        
        # 2. Parse Category
        cat = "General"
        m_cat = re.search(r'🏷\s*([^📅\n\r|]+)', clean)
        if m_cat:
            cat = m_cat.group(1).strip()
        else:
            m_cat2 = re.search(r'(?:^|\n)\s*([A-Za-z &]+)\s*(?:\||\s{2,}📅)\s*', clean)
            if m_cat2:
                cat = m_cat2.group(1).strip()
                
        # 3. Parse Date and Time
        tx_date = get_current_time_in_tz().date()
        tx_time = ""
        m_date = re.search(r'📅\s*([^\n\r🏦]+)', clean)
        date_raw = m_date.group(1).strip() if m_date else ""
        if not date_raw:
            m_date2 = re.search(r'(\d{1,2}\s+[A-Za-z]{3}(?:,?\s*\d{1,2}:\d{2}\s*(?:[APap][Mm])?)?)', clean)
            if m_date2:
                date_raw = m_date2.group(1).strip()
                
        if date_raw:
            m_time = re.search(r'(\d{1,2}:\d{2}\s*(?:[APap][Mm])?)', date_raw)
            if m_time:
                tx_time = m_time.group(1).strip()
                date_only = date_raw.replace(m_time.group(0), '').strip(' ,')
            else:
                date_only = date_raw.strip(' ,')
                
            parsed_d = parse_date(date_only)
            if parsed_d:
                tx_date = parsed_d
                
        # 4. Parse Bank & Reference Number
        bank_name = ""
        ref_num = ""
        m_ref = re.search(r'(?:Ref|ref)[^\d\n\r]*(\d{3,})', clean, re.IGNORECASE)
        if not m_ref:
            m_ref = re.search(r'[…\.]{2,}\s*(\d{3,})', clean)
        if m_ref:
            ref_num = m_ref.group(1).strip()

        m_bank = re.search(r'🏦\s*([^\n\r·]+)', clean)
        if m_bank:
            bank_name = m_bank.group(1).strip()
        else:
            m_bank2 = re.search(r'(?:^|\n)\s*([A-Za-z0-9 ]+(?:Bank|GPay|PhonePe|Paytm|Cred|UPI)[A-Za-z0-9 ]*)', clean, re.IGNORECASE)
            if m_bank2:
                bank_name = m_bank2.group(1).strip()
                bank_name = re.sub(r'\s*(?:Ref|ref|[…\.]{2,}).*', '', bank_name).strip()
                    
        tx = Transaction(
            amount=amount,
            transaction_type=tx_type,
            person_name=person,
            sender_name=person if tx_type == "RECEIVED" else "",
            recipient_name=person if tx_type == "SENT" else "",
            category=cat or "General",
            transaction_date=tx_date,
            transaction_time=tx_time,
            bank_name=bank_name,
            reference_number=ref_num
        )
        return tx
    except Exception as e:
        logger.error(f"Error in reconstruct_transaction_from_card: {e}", exc_info=True)
        return None

# In-memory store for staged (not yet confirmed) JSON import payloads
# Maps token -> {'path': Path, 'preview': dict, 'created_at': float, 'uploader_id': int, 'workspace_id': str}
_pending_json_imports: dict = {}

def _prune_pending_json_imports():
    now_ts = time.time()
    for tok, info in list(_pending_json_imports.items()):
        if now_ts - info.get('created_at', 0) > 600:
            expired = _pending_json_imports.pop(tok, None)
            if expired and expired.get('path') and expired['path'].exists():
                try:
                    expired['path'].unlink()
                except OSError:
                    pass

def format_receipt_card(transaction, dup_warning: str = None) -> str:
    """Formats the polished receipt confirmation card requested by user."""
    amt_str = format_currency(transaction.amount)
    arrow = "←" if transaction.transaction_type == "RECEIVED" else "→"
    person = transaction.person_name or "Unknown"
    cat = transaction.category or "General"
    
    if hasattr(transaction.transaction_date, 'strftime'):
        d_str = transaction.transaction_date.strftime("%d %b")
    else:
        d_str = str(transaction.transaction_date or "Today")
    if transaction.transaction_time:
        d_str += f", {transaction.transaction_time}"

    bank_name = transaction.bank_name or transaction.payment_app or "UPI"
    ref_suffix = f" · Ref …{str(transaction.reference_number)[-4:]}" if transaction.reference_number and len(str(transaction.reference_number)) >= 4 else ""
    
    lines = [
        "🧾 <b>Payment detected</b>",
        "━━━━━━━━━━━━━━",
        f"💸 <b>{amt_str}</b> {arrow} <b>{html.escape(person)}</b>",
        f"🏷 {html.escape(cat)}   📅 {html.escape(d_str)}",
        f"🏦 {html.escape(bank_name)}{html.escape(ref_suffix)}"
    ]
    if dup_warning:
        lines.append(f"\n{dup_warning}")
    return "\n".join(lines)

def parse_short_entry(text: str):
    """Parses short quick entries like '120 dosa', '+500 salary', '-25 snacks', 'coffee 15'."""
    from utils.validation import parse_decimal_amount
    text = text.strip()
    # Patterns like "+500 salary", "500 salary", "120 dosa", "-20 tea"
    m = re.match(r'^([+-]?)\s*(?:₹|rs\.?)?\s*(\d+(?:\.\d+)?)\s+(?:for\s+|to\s+|from\s+)?(.+)$', text, re.IGNORECASE)
    if not m:
        # Also try reverse: "dosa 120", "coffee 15"
        m_rev = re.match(r'^(.+?)\s+([+-]?)\s*(?:₹|rs\.?)?\s*(\d+(?:\.\d+)?)$', text, re.IGNORECASE)
        if m_rev:
            name = m_rev.group(1).strip()
            sign = m_rev.group(2)
            try:
                amt = float(parse_decimal_amount(m_rev.group(3), allow_zero=False))
            except ValueError:
                return None
            tx_type = "RECEIVED" if sign == '+' else "SENT"
            return amt, tx_type, name
        return None
    sign = m.group(1)
    try:
        amt = float(parse_decimal_amount(m.group(2), allow_zero=False))
    except ValueError:
        return None
    name = m.group(3).strip()
    tx_type = "RECEIVED" if sign == '+' else "SENT"
    return amt, tx_type, name

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

async def safe_edit_callback_message(query, text: str, reply_markup=None, parse_mode='HTML'):
    """
    Safely edits a callback query's message, supporting both text and media messages (captions),
    with automatic plain-text fallback on parse errors and fallback to reply_text.
    """
    async def _invoke(fn, *args, **kwargs):
        if not callable(fn):
            return None
        res = fn(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res

    msg = getattr(query, 'message', None)
    is_media = False
    if msg:
        has_text = isinstance(getattr(msg, 'text', None), str)
        has_caption = isinstance(getattr(msg, 'caption', None), str)
        if has_text:
            is_media = False
        elif has_caption:
            is_media = True
        elif getattr(msg, 'photo', None) is not None and type(msg.photo).__name__ not in ('MagicMock', 'Mock'):
            is_media = True

    # 1. Primary edit attempt
    try:
        if is_media and hasattr(query, 'edit_message_caption'):
            await _invoke(query.edit_message_caption, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await _invoke(query.edit_message_text, text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as e1:
        logger.warning(f"Safe edit failed primary attempt (is_media={is_media}, err={e1}). Retrying plain text...")

    # 2. Plain text retry (in case of HTML parsing error or unsupported tags)
    clean_text = re.sub(r'<[^>]+>', '', text)
    try:
        if is_media and hasattr(query, 'edit_message_caption'):
            await _invoke(query.edit_message_caption, caption=clean_text, reply_markup=reply_markup)
        else:
            await _invoke(query.edit_message_text, clean_text, reply_markup=reply_markup)
        return
    except Exception as e2:
        logger.warning(f"Safe edit failed plain attempt (is_media={is_media}, err={e2}). Swapping media/text...")

    # 3. Swap media/text mode in case is_media check had false positive/negative
    try:
        if not is_media and hasattr(query, 'edit_message_caption'):
            await _invoke(query.edit_message_caption, caption=clean_text, reply_markup=reply_markup)
            return
        elif is_media and hasattr(query, 'edit_message_text'):
            await _invoke(query.edit_message_text, clean_text, reply_markup=reply_markup)
            return
    except Exception:
        pass

    # 4. Fallback to sending a new reply if edit was completely rejected
    if msg and hasattr(msg, 'reply_text'):
        try:
            await _invoke(msg.reply_text, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            try:
                await _invoke(msg.reply_text, clean_text, reply_markup=reply_markup)
            except Exception as final_err:
                logger.error(f"Safe edit ultimate reply fallback failed: {final_err}")

ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming images with Gemini Vision AI first, RapidOCR + Heuristic Parser fallback."""
    if not (await require_admin(update, silent=True) or await require_member(update)): return
    
    message = update.message
    chat_id = str(message.chat_id)
    message_id = str(message.message_id)
    caption = message.caption or ""
    
    # 1. Image validation & extension check
    file_id = None
    ext = ".jpg"
    
    if message.photo:
        photo_obj = message.photo[-1]
        if photo_obj.file_size and photo_obj.file_size > MAX_IMAGE_FILE_SIZE:
            await message.reply_text("⚠️ Image file is too large (exceeds 20MB limit).")
            return
        file_id = photo_obj.file_id
        ext = ".jpg"
    elif message.document:
        doc = message.document
        if doc.file_size and doc.file_size > MAX_IMAGE_FILE_SIZE:
            await message.reply_text("⚠️ Image file is too large (exceeds 20MB limit).")
            return
        doc_mime = (doc.mime_type or "").lower()
        orig_ext = os.path.splitext(doc.file_name or "")[1].lower()
        if orig_ext in ALLOWED_IMAGE_EXTENSIONS:
            ext = orig_ext
        elif doc_mime in {'image/jpeg', 'image/jpg'}:
            ext = '.jpg'
        elif doc_mime == 'image/png':
            ext = '.png'
        elif doc_mime == 'image/webp':
            ext = '.webp'
        else:
            await message.reply_text("⚠️ Unsupported image format. Allowed formats: JPG, PNG, WEBP.")
            return
        file_id = doc.file_id
    else:
        return # Ignore non-images
        
    status_msg = await message.reply_text("🔍 Reading receipt…")
    image_path = None
    
    try:
        # Safe UUID-based filename (never use Telegram filename as local path)
        filename = f"rcpt_{uuid.uuid4().hex}{ext}"
        image_path = IMAGE_DIR / filename
        
        # Download image with resilient timeout
        for attempt in range(2):
            try:
                file = await context.bot.get_file(file_id, read_timeout=30.0, connect_timeout=15.0)
                await file.download_to_drive(custom_path=image_path, read_timeout=30.0, connect_timeout=15.0)
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
        gemini_rate_limited = False

        # Tier 1: Try Google Gemini Vision AI first (no OCR pre-processing needed)
        if is_gemini_available():
            try:
                logger.info("Attempting Gemini Vision extraction (Gemini-first, no OCR pre-pass)...")
                g_tx, g_conf = await asyncio.to_thread(
                    extract_transaction_with_gemini, str(image_path), caption, None
                )
                from ocr.gemini_vision import get_last_extraction_error
                if get_last_extraction_error() == "RATE_LIMIT":
                    gemini_rate_limited = True

                if g_tx and g_conf >= 40:
                    transaction = g_tx
                    confidence = g_conf
                    transaction.telegram_message_id = message_id
                    transaction.telegram_chat_id = chat_id
                    transaction.original_image_path = str(image_path)
                    logger.info(f"Gemini Vision succeeded with confidence {g_conf}.")
            except Exception as gem_err:
                logger.warning(f"Gemini Vision error, falling back to RapidOCR parser: {gem_err}")
        else:
            logger.warning("Gemini Vision skipped — API key not configured or empty. Falling back to local OCR.")

        # Tier 2: Fallback to local RapidOCR + Regex Heuristic Parser (only if Gemini failed/skipped)
        if not transaction or not transaction.amount:
            logger.info("Gemini did not return a usable result — running RapidOCR fallback.")
            ocr_text = await perform_ocr_async(str(image_path))
            if not ocr_text or not ocr_text.strip():
                quota_warning = ""
                if gemini_rate_limited:
                    quota_warning = (
                        "⚠️ <b>Gemini Vision daily quota exceeded for today (429 Rate Limit).</b>\n"
                        "Local RapidOCR fallback also could not read text from this image.\n\n"
                    )
                await deliver_response(
                    status_msg, message,
                    f"{quota_warning}❌ Could not read text from this image.\n\n"
                    "💡 <b>Tip:</b> You can log it manually instead:\n"
                    "<code>Paid 2000 to Kanagarla Sai Akhil Amazon Pay</code>\n"
                    "or just <code>2000 sent Kanagarla</code>",
                    parse_mode='HTML'
                )
                return

            transaction, confidence = await asyncio.to_thread(
                process_transaction, ocr_text, str(image_path), message_id, chat_id, caption
            )


        # Basic validation
        if not transaction or not transaction.amount or transaction.amount <= 0:
            quota_warning = ""
            if gemini_rate_limited:
                quota_warning = (
                    "⚠️ <b>Gemini Vision daily quota exceeded for today (429 Rate Limit).</b>\n"
                    "Local RapidOCR fallback could not detect a valid amount from the receipt.\n\n"
                )
            await deliver_response(
                status_msg, message,
                f"{quota_warning}⚠️ Could not detect a valid amount from the receipt.\n\n💡 <b>Tip:</b> You can log it instantly by typing:\n<code>120 dosa</code> or <code>Paid 500 to Ramesh</code>",
                parse_mode='HTML'
            )
            return
            
        if not transaction.transaction_type or transaction.transaction_type == "UNKNOWN":
            await deliver_response(
                status_msg, message,
                f"⚠️ Transaction type (SENT/RECEIVED) could not be determined reliably.\nAmount found: {format_currency(transaction.amount)}"
            )
            return

        ws_ctx = get_workspace_context(update)
        ws_id = ws_ctx.workspace_id if ws_ctx else None
        if transaction:
            transaction.workspace_id = ws_id
            if message.from_user:
                transaction.telegram_user_id = int(message.from_user.id)

        # 1. Payee Category Memory: check if payee category is remembered from past
        rem_cat = await asyncio.to_thread(get_payee_category, transaction.person_name, ws_id)
        if rem_cat:
            transaction.category = rem_cat
        elif not transaction.category or transaction.category == 'General':
            from services.cafeteria_service import is_cafeteria_payment
            if is_cafeteria_payment(transaction.person_name, transaction.upi_id, transaction.ocr_text):
                transaction.category = "Food & Dining"

        # 2. Duplicate Check: compare reference number or amount/person/date against existing rows
        dup = await asyncio.to_thread(
            find_potential_duplicate,
            amount=transaction.amount,
            reference_number=transaction.reference_number,
            person_name=transaction.person_name,
            tx_date=str(transaction.transaction_date) if transaction.transaction_date else None,
            workspace_id=ws_id
        )
        dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None

        # 3. Store in pending for interactive confirmation
        pending_id = uuid.uuid4().hex
        set_pending_transaction(pending_id, transaction, workspace_id=ws_id)

        # 4. Render the polished Confirmation Card requested by user
        card_text = format_receipt_card(transaction, dup_warning)
        if gemini_rate_limited:
            card_text += "\n\nℹ️ <i>Extracted with local RapidOCR (Gemini daily quota reached).</i>"
        card_markup = get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup))

        await deliver_response(status_msg, message, card_text, reply_markup=card_markup, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Error handling image: {e}", exc_info=True)
        await deliver_response(status_msg, message, "❌ Error processing image. Please try again or type the expense manually.")
    finally:
        # Guarantee deletion of temporary file in finally block
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
                logger.info(f"Cleaned up temporary image in finally: {image_path}")
            except OSError as cleanup_err:
                logger.warning(f"Could not delete temp image {image_path}: {cleanup_err}")


CALLBACK_PARAM_SPECS = {
    "undo_tx": [int],
    "select_edit": [int],
    "select_delete": [int],
    "edit_field": [int, str],
    "delete_confirm": [int],
    "correct_amount": [int],
    "cafe_edit": [int],
    "cafe_del_item": [int],
    "cafe_pick": [str],
    "cafe_mode": [str],
    "cafe_cart_add": [str],
    "cafe_cat": [str],
    "cafe_addon": [str],
    "rec_paid": [int],
    "rec_skip": [int],
    "rec_pause": [int],
    "rec_resume": [int],
    "rec_del": [int],
    "rec_cancel": [int],
    "rec_delete": [int],
    "auth_grant": [int, str],
    "auth_deny": [int],
    "perm_view": [int],
    "perm_set": [int, str],
    "perm_remove": [int],
    "perm_remove_confirm": [int],
    "close_month": [int, int],
    "tx_view": [int],
    "dup_tx": [int],
    "edit_tx": [int],
    "delete_tx": [int],
    "qa_payee": [str],
    "save_p": [str],
    "force_save_p": [str],
    "edit_p": [str],
    "ep_field": [str, str],
    "ep_back": [str],
    "cat_p": [str],
    "set_pcat": [str, str],
    "ws_switch": [str],
    "quick_add": [str, float, str],
    "confirm_tx": [str],
    "cancel_tx": [str],
    "export_file": [str],
    "filter": [str],
    "sort": [str],
}


async def _dispatch_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Internal dispatcher for button presses from inline keyboards with central authorization policy."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    parts = data.split(":") if ":" in data else [data]
    action = parts[0]

    # Check policy before any database access or data exposure
    policy = WORKSPACE_CALLBACK_POLICY.get(action) or get_callback_policy(action)
    if policy is None:
        # Unknown or stale callback data gets a friendly refusal, not a crash
        try:
            await query.answer("ℹ️ This button or menu is no longer active.", show_alert=True)
        except Exception:
            pass
        return

    ws_ctx = get_workspace_context(update)
    if policy == 'owner':
        if not await require_owner(update):
            return
    elif policy == 'admin':
        if not (is_owner(update) or await require_admin(update)):
            return
    elif policy == 'member':
        if not (is_owner(update) or is_admin_user(update) or await require_member(update)):
            return
    elif policy in ('read_only', 'viewer'):
        if not (is_owner(update) or is_admin_user(update) or await require_authorized(update)):
            return
    else:
        ws_ctx = await resolve_workspace_context(update, required_policy=policy)
        if not ws_ctx:
            return

    # Validate argument specifications at dispatch (B4 / P1-11)
    if action in CALLBACK_PARAM_SPECS:
        specs = CALLBACK_PARAM_SPECS[action]
        if len(parts) - 1 < len(specs):
            try:
                ans = query.answer("❌ Invalid button data.", show_alert=True)
                if asyncio.iscoroutine(ans):
                    await ans
            except Exception:
                pass
            return
        is_valid = True
        for idx, expected_type in enumerate(specs, start=1):
            val = parts[idx]
            if expected_type is int:
                try:
                    int(val)
                except (ValueError, TypeError):
                    is_valid = False
                    break
            elif expected_type is float:
                try:
                    float(val)
                except (ValueError, TypeError):
                    is_valid = False
                    break
            elif expected_type is str:
                if not val or not str(val).strip():
                    is_valid = False
                    break
        if not is_valid:
            try:
                ans = query.answer("❌ Invalid button data.", show_alert=True)
                if asyncio.iscoroutine(ans):
                    await ans
            except Exception:
                pass
            return

    if action not in ("set_model", "refresh_gemini") and not action.startswith("perm_"):
        try:
            await query.answer()
        except Exception:
            pass
    
    if not ws_ctx:
        logger.warning(f"Could not resolve workspace context for action '{action}'. Rejecting callback.")
        try:
            await query.answer("❌ Workspace context not found.", show_alert=True)
        except Exception:
            pass
        return
    ws_id = ws_ctx.workspace_id

    # Enforce unified undo authorization at dispatch (B9 / P1-N2)
    if action in ("undo_action", "undo_confirm", "undo_cancel"):
        caller_id = update.effective_user.id if update.effective_user else None
        if not (is_owner(update) or is_admin_user(update)):
            from database.db import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT user_id FROM undo_log WHERE (workspace_id = ? OR workspace_id IS NULL) AND used_at IS NULL ORDER BY id DESC LIMIT 1",
                    (ws_id,)
                )
                row = cursor.fetchone()
                if not row or row["user_id"] != caller_id:
                    try:
                        ans = query.answer("⛔ You can only undo your own actions.", show_alert=True)
                        if asyncio.iscoroutine(ans):
                            await ans
                    except Exception:
                        pass
                    return

    # --- 0. Interactive Home Menu Navigation ---
    if action == "nav":
        from bot.callbacks import handle_nav_callback
        await handle_nav_callback(query, context, parts, ws_id, ws_ctx, update)
        return


    # --- 1. Transaction Detail, Duplicate & Backup Actions ---
    elif action == "close_month":
        y = int(parts[1])
        m = int(parts[2])
        if not (2000 <= y <= 2100 and 1 <= m <= 12):
            await query.answer("❌ Invalid month or year range.", show_alert=True)
            return
        from services.monthly_review_service import close_and_record_monthly_review
        from bot.commands import render_monthly_closing_summary_text
        from bot.keyboards import get_monthly_closing_keyboard
        await asyncio.to_thread(close_and_record_monthly_review, y, m, workspace_id=ws_id)
        text = await asyncio.to_thread(render_monthly_closing_summary_text, y, m, workspace_id=ws_id)
        caller_role = ws_ctx.role if ws_ctx else 'viewer'
        await query.edit_message_text(text, reply_markup=get_monthly_closing_keyboard(y, m, is_closed=True, role=caller_role), parse_mode='HTML')
        return

    elif action == "rec_paid":
        rec_id = int(parts[1])
        from services.recurring_service import mark_recurring_paid, get_recurring_by_id
        try:
            tx_id, next_due = await asyncio.to_thread(mark_recurring_paid, rec_id, None, ws_id)
            rec = await asyncio.to_thread(get_recurring_by_id, rec_id, ws_id)
            if not rec:
                await query.answer("❌ Recurring payment not found.", show_alert=True)
                return
        except ValueError as err:
            await query.answer(f"❌ {err}", show_alert=True)
            return
        text = (
            f"✅ <b>Recurring Payment Logged to Ledger!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Payee:</b> {html.escape(rec['payee_name'])}\n"
            f"• <b>Amount:</b> {format_currency(rec['amount'])}\n"
            f"• <b>Transaction Created:</b> #{tx_id}\n"
            f"• <b>New Next Due Date:</b> <code>{next_due}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(text, reply_markup=get_quick_undo_keyboard(tx_id), parse_mode='HTML')
        return

    elif action == "rec_skip":
        rec_id = int(parts[1])
        from services.recurring_service import skip_recurring_due, get_recurring_by_id
        try:
            next_due = await asyncio.to_thread(skip_recurring_due, rec_id, ws_id)
            rec = await asyncio.to_thread(get_recurring_by_id, rec_id, ws_id)
            if not rec:
                await query.answer("❌ Recurring payment not found.", show_alert=True)
                return
        except ValueError as err:
            await query.answer(f"❌ {err}", show_alert=True)
            return
        text = (
            f"⏭️ <b>Recurring Cycle Skipped</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Payee:</b> {html.escape(rec['payee_name'])}\n"
            f"• <b>New Next Due Date:</b> <code>{next_due}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "rec_pause":
        rec_id = int(parts[1])
        from services.recurring_service import update_recurring_status
        await asyncio.to_thread(update_recurring_status, rec_id, 'PAUSED', ws_id)
        await query.edit_message_text("⏸️ <b>Recurring payment paused.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "rec_resume":
        rec_id = int(parts[1])
        from services.recurring_service import update_recurring_status
        await asyncio.to_thread(update_recurring_status, rec_id, 'ACTIVE', ws_id)
        await query.edit_message_text("▶️ <b>Recurring payment resumed.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "rec_del":
        rec_id = int(parts[1])
        from services.recurring_service import delete_recurring_payment
        await asyncio.to_thread(delete_recurring_payment, rec_id, ws_id)
        await query.edit_message_text("🗑️ <b>Recurring payment deleted.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return


    elif action == "restore_confirm":
        from services.backup_service import import_database_from_json, backup_to_telegram
        from database.queries import get_all_transactions, get_default_workspace_id
        default_ws = get_default_workspace_id()
        is_custom_ws = (ws_id and ws_id != default_ws)
        target_ws_id = ws_id if is_custom_ws else None
        scope_label = f"Workspace ({ws_id})" if is_custom_ws else "Global Database"
        await query.edit_message_text(f"⏳ <b>Restoring {html.escape(scope_label)} from backup...</b>", parse_mode='HTML')
        result = await asyncio.to_thread(import_database_from_json, target_workspace_id=target_ws_id)
        if not result.get('success'):
            await query.edit_message_text(f"❌ <b>Restore Failed:</b> {html.escape(str(result.get('error')))}", parse_mode='HTML')
            return
        await asyncio.to_thread(recalculate_all_balances, workspace_id=target_ws_id)
        await backup_to_telegram(context.bot)
        txs = await asyncio.to_thread(get_all_transactions, workspace_id=target_ws_id)
        cur_b = format_currency(get_balance_setting(workspace_id=target_ws_id))
        await query.edit_message_text(
            f"✅ <b>Database Restored Successfully!</b>\n\n"
            f"• <b>Scope:</b> {html.escape(scope_label)}\n"
            f"• <b>{len(txs)}</b> live transactions available.\n"
            f"• <b>Current Balance:</b> {cur_b}\n\n"
            f"Use <code>/balance</code> or <code>/history</code> to view your ledger.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
        return

    elif action == "restore_cancel":
        await query.edit_message_text("❌ <b>Restore Cancelled.</b>", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "json_import_confirm":
        # Confirm a staged JSON import (from handle_document preview flow).
        _prune_pending_json_imports()
        token = parts[1] if len(parts) > 1 else ""
        staged = _pending_json_imports.get(token)
        if not staged:
            await query.edit_message_text(
                "❌ <b>Import session expired or not found.</b>\nPlease re-upload the backup file.",
                reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML'
            )
            return

        caller_id = update.effective_user.id if update.effective_user else None
        uploader_id = staged.get('uploader_id')
        if uploader_id and caller_id != uploader_id and not is_owner(update):
            try:
                ans = query.answer("⛔ Only the uploader or bot owner can confirm this import.", show_alert=True)
                if asyncio.iscoroutine(ans):
                    await ans
            except Exception:
                pass
            return

        _pending_json_imports.pop(token, None)
        tmp_path = staged['path']
        staged_ws_id = staged.get('workspace_id') or ws_id
        await query.edit_message_text("⏳ <b>Importing backup into ledger…</b>", parse_mode='HTML')
        try:
            from services.backup_service import import_database_from_json, export_database_to_json, backup_to_telegram
            from database.queries import get_all_transactions

            # Step 1: Import with workspace scoping
            result = await asyncio.to_thread(import_database_from_json, input_path=tmp_path, target_workspace_id=staged_ws_id)
            if not result.get('success'):
                await query.edit_message_text(
                    f"❌ <b>Import Failed:</b> {html.escape(str(result.get('error', 'Unknown error')))}",
                    reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML'
                )
                return

            # Step 2: Recalculate balances with workspace scoping
            await asyncio.to_thread(recalculate_all_balances, workspace_id=staged_ws_id)

            # Step 3: Export a FRESH backup from the live DB → this becomes the new BACKUP_JSON_PATH
            from config import BACKUP_JSON_PATH
            await asyncio.to_thread(export_database_to_json, BACKUP_JSON_PATH)

            # Step 4: Cloud-upload the fresh backup
            await backup_to_telegram(context.bot)

            # Step 5: Report result scoped to workspace
            txs = await asyncio.to_thread(get_all_transactions, workspace_id=staged_ws_id)
            cur_b = format_currency(get_balance_setting(workspace_id=staged_ws_id))
            ins = result.get('inserted', 0)
            upd = result.get('updated', 0)
            skp = result.get('skipped', 0)
            bal_match = result.get('balance_match', True)
            bal_note = "" if bal_match else "\n⚠️ Balance mismatch detected — please verify with /balance."
            await query.edit_message_text(
                f"✅ <b>Import Successful!</b>\n\n"
                f"• ➕ Added: {ins}   ✏️ Updated: {upd}   ⏩ Skipped: {skp}\n"
                f"• <b>{len(txs)}</b> live transactions in ledger.\n"
                f"• <b>Current Balance:</b> {cur_b}{bal_note}\n\n"
                f"A fresh backup has been exported and uploaded to cloud.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error during json_import_confirm: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ <b>Import error:</b> {html.escape(str(e))}",
                reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML'
            )
        finally:
            # Always remove the temp file
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        return

    elif action == "json_import_cancel":
        _prune_pending_json_imports()
        token = parts[1] if len(parts) > 1 else ""
        staged = _pending_json_imports.get(token)
        if staged:
            caller_id = update.effective_user.id if update.effective_user else None
            uploader_id = staged.get('uploader_id')
            if uploader_id and caller_id != uploader_id and not is_owner(update):
                try:
                    ans = query.answer("⛔ Only the uploader or bot owner can cancel this import.", show_alert=True)
                    if asyncio.iscoroutine(ans):
                        await ans
                except Exception:
                    pass
                return
            _pending_json_imports.pop(token, None)
            if staged.get('path') and staged['path'].exists():
                try:
                    staged['path'].unlink()
                except OSError:
                    pass
        await query.edit_message_text(
            "❌ <b>Import cancelled.</b> No changes were made.",
            reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML'
        )
        return

    # --- 1. Transaction Detail, Duplicate & Backup Actions ---
    elif action == "tx_view":
        tx_id = int(parts[1])
        caller_role = ws_ctx.role if ws_ctx else 'viewer'
        text, markup = render_transaction_detail(tx_id, workspace_id=ws_id, role=caller_role)
        await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
        return

    elif action == "dup_tx":
        tx_id = int(parts[1])
        orig_tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not orig_tx:
            await query.edit_message_text("❌ Transaction not found.", reply_markup=get_back_to_menu_keyboard())
            return
        
        from database.models import Transaction
        dup_pending_id = uuid.uuid4().hex
        dup_tx = Transaction(
            amount=orig_tx['amount'],
            transaction_type=orig_tx['transaction_type'],
            person_name=orig_tx.get('person_name'),
            category=orig_tx.get('category') or 'General',
            payment_app=orig_tx.get('payment_app'),
            bank_name=orig_tx.get('bank_name'),
            workspace_id=ws_id
        )
        set_pending_transaction(dup_pending_id, dup_tx, workspace_id=ws_id)
        card_text = format_receipt_card(dup_tx, dup_warning=f"📋 Duplicating Transaction #{tx_id}")
        await query.edit_message_text(card_text, reply_markup=get_confirmation_card_keyboard(dup_pending_id), parse_mode='HTML')
        return

    elif action == "edit_tx":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not tx:
            await query.edit_message_text(
                f"❌ <b>Transaction Not Found</b>\nTransaction #{tx_id} could not be found.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
            return

        caller_id = update.effective_user.id if update.effective_user else 0
        caller_role = ws_ctx.role if ws_ctx else 'viewer'
        from database.queries import can_user_modify_transaction
        if not can_user_modify_transaction(tx_id, user_id=caller_id, user_role=caller_role, workspace_id=ws_id):
            await query.answer("⛔ You do not have permission to edit this transaction.", show_alert=True)
            return

        await query.edit_message_text(
            f"✏️ <b>Edit Transaction #{tx_id}:</b>\nChoose which field you want to modify:",
            reply_markup=get_edit_fields_keyboard(tx_id),
            parse_mode='HTML'
        )
        return

    elif action == "delete_tx":
        tx_id = int(parts[1])
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not tx:
            await query.edit_message_text(
                f"❌ <b>Transaction Not Found</b>\nTransaction #{tx_id} could not be found.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
            return

        amt_str = format_currency(tx['amount'])
        person = tx['person_name'] or "Unknown"
        tx_type = tx.get('transaction_type', 'TRANSACTION')
        sign = "-" if tx_type == 'SENT' else "+"
        tx_date = tx.get('transaction_date') or "Today"

        prompt = (
            f"🗑️ <b>Delete Transaction #{tx_id}?</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"• <b>Person:</b> <b>{html.escape(str(person))}</b>\n"
            f"• <b>Amount:</b> <b>{sign}{html.escape(amt_str)}</b> ({html.escape(str(tx_type))})\n"
            f"• <b>Date:</b> {html.escape(str(tx_date))}\n"
            "━━━━━━━━━━━━━━\n"
            "⚠️ <i>Are you sure you want to delete this transaction from your ledger?</i>"
        )
        await query.edit_message_text(
            prompt,
            reply_markup=get_delete_confirm_keyboard(tx_id),
            parse_mode='HTML'
        )
        return

    elif action == "backup_now":
        await query.edit_message_text("⏳ <i>Exporting database and uploading cloud backup...</i>", parse_mode='HTML')
        from services.backup_service import export_database_to_json, backup_to_telegram
        export_database_to_json()
        success = await backup_to_telegram(context.bot, force=True)
        if success:
            text = "✅ <b>Cloud Backup Completed Successfully!</b>\n\n" + render_backup_status_text()
            await query.edit_message_text(text, reply_markup=get_backup_status_keyboard(), parse_mode='HTML')
        else:
            await query.edit_message_text("⚠️ Cloud backup failed or no active records to backup.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "qa_payee":
        payee = parts[1]
        tt = parts[2] if len(parts) > 2 else "SENT"
        context.user_data['action'] = 'waiting_payee_amount'
        context.user_data['quick_payee'] = payee
        context.user_data['quick_type'] = tt
        arrow = "to" if tt == "SENT" else "from"
        await query.edit_message_text(
            f"💵 <b>Quick Add:</b> Enter amount {arrow} <b>{html.escape(payee)}</b> (e.g. <code>250</code>):",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
        return

    # --- 1. Redesigned Receipt Card Actions ---
    elif action in ("save_p", "force_save_p"):
        is_force = (action == "force_save_p")
        pending_id = parts[1]
        transaction = pop_pending_transaction(pending_id, workspace_id=ws_id) if not is_force else fetch_pending_transaction(pending_id, workspace_id=ws_id)
        if is_force:
            pop_pending_transaction(pending_id, workspace_id=ws_id)

        if not transaction:
            other = get_pending_receipt(pending_id)
            if other and str(getattr(other, 'workspace_id', '') or '') != str(ws_id):
                try:
                    ans = query.answer("⚠️ This receipt belongs to another workspace. Switch back to confirm it.", show_alert=True)
                    if asyncio.iscoroutine(ans):
                        await ans
                except Exception:
                    pass
                return
            msg_text = (query.message.text if query.message else "") or (query.message.caption if query.message else "")
            transaction = reconstruct_transaction_from_card(msg_text)
            if transaction:
                transaction.workspace_id = str(other.workspace_id) if other else ws_id
                logger.info(f"Reconstructed transaction from receipt card text: {transaction}")
        if not transaction:
            try:
                ans = query.answer("❌ Receipt confirmation expired.", show_alert=True)
                if asyncio.iscoroutine(ans):
                    await ans
            except Exception:
                pass
            await safe_edit_callback_message(query, "❌ Receipt confirmation expired.", reply_markup=get_back_to_menu_keyboard())
            return


        # Bind pendings to author: only author or admin/owner can confirm
        creator_uid = getattr(transaction, 'telegram_user_id', None)
        clicker_uid = query.from_user.id if query.from_user else None
        if creator_uid and clicker_uid and int(creator_uid) != int(clicker_uid):
            from bot.auth import is_admin_or_owner
            if not is_admin_or_owner(update, workspace_id=ws_id):
                # Put back into pending since we popped it
                set_pending_transaction(pending_id, transaction, workspace_id=ws_id)
                try:
                    ans = query.answer("⛔ Only the creator of this receipt or an admin can confirm it.", show_alert=True)
                    if asyncio.iscoroutine(ans):
                        await ans
                except Exception:
                    pass
                return

        # Check if this exact receipt/transaction was already saved in the database
        existing = None
        if not is_force:
            if transaction.reference_number:
                existing = get_transaction_by_reference(transaction.reference_number, workspace_id=ws_id)
            if not existing:
                pot_dup = find_potential_duplicate(
                    amount=transaction.amount,
                    reference_number=transaction.reference_number,
                    person_name=transaction.person_name,
                    tx_date=str(transaction.transaction_date) if transaction.transaction_date else None,
                    workspace_id=ws_id
                )
                if pot_dup and pot_dup.get('match_reason') and 'ref' in pot_dup.get('match_reason', ''):
                    existing = pot_dup

        if existing and not is_force:
            # IT IS ALREADY SAVED! Keep pending so user can force-save if desired
            try:
                ans = query.answer("ℹ️ Already Saved!", show_alert=False)
                if asyncio.iscoroutine(ans):
                    await ans
            except Exception:
                pass
            set_pending_transaction(pending_id, transaction, workspace_id=ws_id)
            date_display = existing.get('transaction_date') or "Today"
            if existing.get('transaction_time'):
                date_display += f", {existing['transaction_time']}"
            arrow = "←" if existing.get('transaction_type') == "RECEIVED" else "→"
            ref_line = f"\n🔢 <b>Ref:</b> <code>{html.escape(str(existing.get('reference_number')))}</code>" if existing.get('reference_number') else ""
            person = existing.get('person_name') or 'Unknown'
            cat = existing.get('category') or 'General'
            bal = format_currency(existing.get('balance_after') or 0.0)
            amt = format_currency(existing.get('amount') or 0.0)

            already_saved_text = (
                f"ℹ️ <b>Already Saved! #{existing['id']}</b>\n"
                "━━━━━━━━━━━━━━\n"
                "This payment is already recorded in your ledger:\n\n"
                f"💸 <b>{amt}</b> {arrow} <b>{html.escape(person)}</b>\n"
                f"🏷 {html.escape(cat)}   📅 {html.escape(str(date_display))}\n"
                f"💼 <b>Current Balance:</b> <b>{bal}</b>"
                f"{ref_line}"
            )
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Save as New Entry", callback_data=f"force_save_p:{pending_id}")],
                [InlineKeyboardButton(f"↩️ Undo #{existing['id']}", callback_data=f"undo_tx:{existing['id']}")],
                [InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")]
            ])
            await safe_edit_callback_message(query, already_saved_text, reply_markup=kb, parse_mode='HTML')
            return
        
        # Remember payee preference in DB if available
        if transaction.person_name and transaction.category:
            remember_payee_category(transaction.person_name, transaction.category, workspace_id=ws_id)
            
        # Bind pendings to the author; never overwrite creator_uid with another clicker
        if not getattr(transaction, 'telegram_user_id', None) and query.from_user and getattr(query.from_user, 'id', None):
            transaction.telegram_user_id = int(query.from_user.id)

        success = commit_transaction(transaction, allow_duplicate=is_force)
        if success:
            try:
                ans = query.answer("✅ Payment Saved!", show_alert=False)
                if asyncio.iscoroutine(ans):
                    await ans
            except Exception:
                pass

            # Record undo action so /undo and instant undo button work
            chat_id = None
            if query.message and getattr(query.message, 'chat_id', None) is not None:
                try:
                    chat_id = int(query.message.chat_id)
                except (ValueError, TypeError):
                    chat_id = None
            user_id = None
            if query.from_user and getattr(query.from_user, 'id', None) is not None:
                try:
                    user_id = int(query.from_user.id)
                except (ValueError, TypeError):
                    user_id = None
            try:
                from services.undo_service import record_insert_action
                record_insert_action(transaction.id, chat_id=chat_id, user_id=user_id, workspace_id=ws_id)
            except Exception as u_err:
                logger.warning(f"Could not record undo action: {u_err}")

            # Fetch fresh transaction from DB to get the exact recalculated balance_after
            saved_tx = get_transaction_by_id(transaction.id, workspace_id=ws_id)
            if saved_tx and saved_tx.get('balance_after') is not None:
                current_bal_val = saved_tx.get('balance_after')
            else:
                current_bal_val = get_balance_setting(workspace_id=ws_id)

            # Check budget alerts (Requirement 5: After saving a transaction, add a one-line alert if a budget crosses 80% or 100%)
            from services.budget_service import get_budget_info
            now = get_current_time_in_tz()
            b_info = get_budget_info(now.year, now.month, workspace_id=ws_id)
            budget_alert = ""
            if b_info.get('budget', 0) > 0:
                pct = b_info.get('percentage', 0)
                spent = b_info.get('spent', 0)
                limit = b_info.get('budget', 0)
                if pct >= 100:
                    budget_alert = f"\n\n🚨 <b>Alert:</b> Budget exceeded! <b>{pct:.0f}%</b> (₹{spent:,.0f} / ₹{limit:,.0f})"
                elif pct >= 80:
                    budget_alert = f"\n\n⚠️ <b>Alert:</b> Budget reached <b>{pct:.0f}%</b> (₹{spent:,.0f} / ₹{limit:,.0f})"

            date_display = transaction.transaction_date.strftime("%d %b") if hasattr(transaction.transaction_date, 'strftime') else (str(transaction.transaction_date) if transaction.transaction_date else "Today")
            if transaction.transaction_time:
                date_display += f", {transaction.transaction_time}"
            arrow = "←" if transaction.transaction_type == "RECEIVED" else "→"
            
            saved_text = (
                f"✅ <b>Payment Saved! #{transaction.id}</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"💸 <b>{format_currency(transaction.amount)}</b> {arrow} <b>{html.escape(transaction.person_name or 'Unknown')}</b>\n"
                f"🏷 {html.escape(transaction.category or 'General')}   📅 {html.escape(date_display)}\n"
                f"💼 <b>Current Balance:</b> <b>{format_currency(current_bal_val)}</b>\n\n"
                f"✅ <i>Saved to your database and backed up to cloud.</i>"
                f"{budget_alert}"
            )
            await safe_edit_callback_message(query, saved_text, reply_markup=get_quick_undo_keyboard(transaction.id), parse_mode='HTML')
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
        else:
            existing = get_transaction_by_reference(transaction.reference_number, workspace_id=ws_id) if transaction.reference_number else None
            if existing:
                try:
                    ans = query.answer("ℹ️ Already Saved!", show_alert=False)
                    if asyncio.iscoroutine(ans):
                        await ans
                except Exception:
                    pass
                date_display = existing.get('transaction_date') or "Today"
                if existing.get('transaction_time'):
                    date_display += f", {existing['transaction_time']}"
                arrow = "←" if existing.get('transaction_type') == "RECEIVED" else "→"
                person = existing.get('person_name') or 'Unknown'
                cat = existing.get('category') or 'General'
                bal = format_currency(existing.get('balance_after') or 0.0)
                amt = format_currency(existing.get('amount') or 0.0)
                already_saved_text = (
                    f"ℹ️ <b>Already Saved! #{existing['id']}</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    "This payment is already recorded in your ledger:\n\n"
                    f"💸 <b>{amt}</b> {arrow} <b>{html.escape(person)}</b>\n"
                    f"🏷 {html.escape(cat)}   📅 {html.escape(str(date_display))}\n"
                    f"💼 <b>Current Balance:</b> <b>{bal}</b>"
                )
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Save as New Entry", callback_data=f"force_save_p:{pending_id}")],
                    [InlineKeyboardButton(f"↩️ Undo #{existing['id']}", callback_data=f"undo_tx:{existing['id']}")],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="nav:home")]
                ])
                set_pending_transaction(pending_id, transaction, workspace_id=ws_id)
                await safe_edit_callback_message(query, already_saved_text, reply_markup=kb, parse_mode='HTML')
            else:
                try:
                    ans = query.answer("⚠️ Save Failed", show_alert=True)
                    if asyncio.iscoroutine(ans):
                        await ans
                except Exception:
                    pass
                await safe_edit_callback_message(query, "⚠️ Transaction could not be saved. Please verify the amount and details or enter manually.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "edit_p":
        pending_id = parts[1]
        transaction = fetch_pending_transaction(pending_id, workspace_id=ws_id)
        creator_uid = getattr(transaction, 'telegram_user_id', None) if transaction else None
        clicker_uid = query.from_user.id if query.from_user else None
        if creator_uid and clicker_uid and int(creator_uid) != int(clicker_uid):
            from bot.auth import is_admin_or_owner
            if not is_admin_or_owner(update, workspace_id=ws_id):
                try:
                    await query.answer("⛔ Only the creator of this receipt or an admin can edit it.", show_alert=True)
                except Exception:
                    pass
                return
        await safe_edit_callback_message(
            query,
            "✏️ <b>Select Field to Edit:</b>\n"
            "Choose which field of the detected receipt you want to change:",
            reply_markup=get_edit_pending_fields_keyboard(pending_id),
            parse_mode='HTML'
        )
        return

    elif action == "ep_field":
        pending_id = parts[1]
        field = parts[2]
        allowed_fields = {'amount', 'person', 'category', 'date', 'type'}
        if field not in allowed_fields:
            await query.answer("❌ Invalid edit field.", show_alert=True)
            return

        transaction = fetch_pending_transaction(pending_id, workspace_id=ws_id)
        creator_uid = getattr(transaction, 'telegram_user_id', None) if transaction else None
        clicker_uid = query.from_user.id if query.from_user else None
        if creator_uid and clicker_uid and int(creator_uid) != int(clicker_uid):
            from bot.auth import is_admin_or_owner
            if not is_admin_or_owner(update, workspace_id=ws_id):
                try:
                    await query.answer("⛔ Only the creator of this receipt or an admin can edit it.", show_alert=True)
                except Exception:
                    pass
                return

        context.user_data['action'] = 'waiting_edit_pending_value'
        context.user_data['pending_id'] = pending_id
        context.user_data['pending_field'] = field
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel Edit", callback_data=f"ep_back:{pending_id}")]])
        prompts = {
            'amount': "Enter the new amount (e.g. <code>150</code>):",
            'person': "Enter the payee / person name:",
            'date': "Enter the date (e.g. <code>yesterday</code> or <code>19/09/2026</code>):",
            'type': "Enter type (<code>SENT</code> or <code>RECEIVED</code>):",
            'category': "Enter category (or choose from category picker):"
        }
        await safe_edit_callback_message(query, f"✏️ {prompts.get(field, 'Enter new value:')}", reply_markup=cancel_markup, parse_mode='HTML')
        return

    elif action == "ep_back":
        pending_id = parts[1]
        transaction = fetch_pending_transaction(pending_id, workspace_id=ws_id)
        if not transaction:
            other = get_pending_receipt(pending_id)
            if other and str(getattr(other, 'workspace_id', '') or '') != str(ws_id):
                try:
                    await query.answer("⚠️ This receipt belongs to another workspace. Switch back to confirm it.", show_alert=True)
                except Exception:
                    pass
                return
            msg_text = (query.message.text if query.message else "") or (query.message.caption if query.message else "")
            transaction = reconstruct_transaction_from_card(msg_text)
            if transaction:
                provenance_ws = str(other.workspace_id) if other else ws_id
                transaction.workspace_id = provenance_ws
                set_pending_transaction(pending_id, transaction, workspace_id=provenance_ws)
        if not transaction:
            await safe_edit_callback_message(query, "❌ Transaction expired.", reply_markup=get_back_to_menu_keyboard())
            return
        dup = find_potential_duplicate(
            amount=transaction.amount,
            reference_number=transaction.reference_number,
            person_name=transaction.person_name,
            tx_date=str(transaction.transaction_date) if transaction.transaction_date else None,
            workspace_id=ws_id
        )
        dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None
        card_text = format_receipt_card(transaction, dup_warning)
        await safe_edit_callback_message(query, card_text, reply_markup=get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup)), parse_mode='HTML')
        return

    elif action == "cat_p":
        pending_id = parts[1]
        await safe_edit_callback_message(
            query,
            "🏷️ <b>Select Category:</b>\n"
            "Tap a category below. Your preference will be remembered for this payee in future receipts:",
            reply_markup=get_category_picker_keyboard(pending_id),
            parse_mode='HTML'
        )
        return

    elif action == "set_pcat":
        pending_id = parts[1]
        new_cat = parts[2]
        from services.category_service import CATEGORIES
        allowed_cats = set(CATEGORIES.keys()) | {
            "Transport", "Healthcare", "Salary", "General", "Income", "Investment", "Personal"
        }
        if new_cat not in allowed_cats:
            await query.answer("❌ Invalid category.", show_alert=True)
            return

        transaction = fetch_pending_transaction(pending_id, workspace_id=ws_id)
        if not transaction:
            other = get_pending_receipt(pending_id)
            if other and str(getattr(other, 'workspace_id', '') or '') != str(ws_id):
                try:
                    await query.answer("⚠️ This receipt belongs to another workspace. Switch back to confirm it.", show_alert=True)
                except Exception:
                    pass
                return
            msg_text = (query.message.text if query.message else "") or (query.message.caption if query.message else "")
            transaction = reconstruct_transaction_from_card(msg_text)
            if transaction and other:
                transaction.workspace_id = str(other.workspace_id)

        creator_uid = getattr(transaction, 'telegram_user_id', None) if transaction else None
        clicker_uid = query.from_user.id if query.from_user else None
        if creator_uid and clicker_uid and int(creator_uid) != int(clicker_uid):
            from bot.auth import is_admin_or_owner
            if not is_admin_or_owner(update, workspace_id=ws_id):
                try:
                    await query.answer("⛔ Only the creator of this receipt or an admin can edit it.", show_alert=True)
                except Exception:
                    pass
                return

        if transaction:
            transaction.category = new_cat
            set_pending_transaction(pending_id, transaction, workspace_id=ws_id)
            if transaction.person_name:
                remember_payee_category(transaction.person_name, new_cat, workspace_id=ws_id)
            dup = find_potential_duplicate(
                amount=transaction.amount,
                reference_number=transaction.reference_number,
                person_name=transaction.person_name,
                tx_date=str(transaction.transaction_date) if transaction.transaction_date else None,
                workspace_id=ws_id
            )
            dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None
            card_text = format_receipt_card(transaction, dup_warning)
            await safe_edit_callback_message(query, card_text, reply_markup=get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup)), parse_mode='HTML')
        else:
            await safe_edit_callback_message(query, "❌ Transaction expired.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "cancel_p":
        pending_id = parts[1]
        transaction = fetch_pending_transaction(pending_id, workspace_id=ws_id)
        creator_uid = getattr(transaction, 'telegram_user_id', None) if transaction else None
        clicker_uid = query.from_user.id if query.from_user else None
        if creator_uid and clicker_uid and int(creator_uid) != int(clicker_uid):
            from bot.auth import is_admin_or_owner
            if not is_admin_or_owner(update, workspace_id=ws_id):
                try:
                    await query.answer("⛔ Only the creator of this receipt or an admin can discard it.", show_alert=True)
                except Exception:
                    pass
                return

        try:
            await query.answer("❌ Receipt discarded.", show_alert=False)
        except Exception:
            pass
        pop_pending_transaction(pending_id, workspace_id=ws_id)
        await safe_edit_callback_message(query, "❌ Receipt discarded.", reply_markup=get_back_to_menu_keyboard())
        return

    # --- Workspace Switcher Actions ---
    elif action == "ws_switch":
        target_ws_id = parts[1]
        from bot.auth import set_user_active_workspace, is_super_admin
        from database.queries import get_workspace_by_id, get_workspace_member
        user_id = get_effective_user_id(update)
        target_ws = get_workspace_by_id(target_ws_id)
        if target_ws and user_id:
            m = get_workspace_member(target_ws.id, user_id)
            is_global_owner = is_super_admin(user_id) or (user_id is not None and str(user_id) == str(getattr(config, 'TELEGRAM_USER_ID', None)))
            if not is_global_owner and not (m and m.is_active and getattr(m, 'status', 'active') == 'active'):
                await query.answer("⛔ You are not an active member of that workspace.", show_alert=True)
                return
            chat_id = getattr(update.effective_chat, 'id', None)
            default_ws_id = get_default_workspace_id()
            if (chat_id is not None and target_ws.chat_id == chat_id) or target_ws.id == default_ws_id:
                set_user_active_workspace(user_id, None)
            else:
                set_user_active_workspace(user_id, target_ws.id)
            try:
                await query.answer(f"Switched to: {target_ws.title}")
            except Exception:
                pass
            from bot.commands import render_workspaces_view
            text, markup = render_workspaces_view(update)
            await safe_edit_callback_message(query, text, reply_markup=markup, parse_mode='HTML')
        else:
            await query.answer("❌ Workspace not found.", show_alert=True)
        return

    elif action in ("ws_reset", "ws_reset_menu"):
        if not query.message:
            try:
                await query.answer("❌ Message no longer accessible.", show_alert=True)
            except Exception:
                pass
            return
        from bot.auth import set_user_active_workspace
        user_id = get_effective_user_id(update)
        if user_id:
            set_user_active_workspace(user_id, None)
        try:
            await query.answer("Reset to chat default!")
        except Exception:
            pass
        from bot.commands import render_workspaces_view
        text, markup = render_workspaces_view(update)
        await safe_edit_callback_message(query, text, reply_markup=markup, parse_mode='HTML')
        return

    elif action == "ws_new_prompt":
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        try:
            await query.answer()
        except Exception:
            pass
        prompt_text = (
            "➕ <b>Create a New Ledger</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "You can create a standalone workspace anytime by typing in chat:\n\n"
            "<code>/workspace create &lt;Name&gt;</code>\n\n"
            "<i>Examples:</i>\n"
            "• <code>/workspace create Goa Trip</code>\n"
            "• <code>/workspace create Side Project</code>\n"
            "• <code>/workspace create Shared Budget</code>\n\n"
            "It will immediately create the workspace and switch your view to it."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Workspaces", callback_data="ws_reset_menu")]
        ])
        await safe_edit_callback_message(query, prompt_text, reply_markup=markup, parse_mode='HTML')
        return

    # --- Access Request & Approval Callbacks ---
    elif action == "auth_grant":
        if not is_owner(update):
            await query.answer("⛔ Only the bot owner can approve access.", show_alert=True)
            return
        target_uid = int(parts[1])
        role = parts[2].lower() if len(parts) > 2 else "member"
        if role not in ('member', 'admin', 'viewer'):
            role = 'member'
        from database.queries import (
            get_access_request, update_access_request_status, set_user_permission_and_role,
            get_workspace_by_chat_id, get_or_create_workspace, add_workspace_member,
            get_workspace_by_id
        )
        from services.audit_service import log_audit_event

        req = get_access_request(target_uid)
        display_name = (req.get('display_name') if req else '') or str(target_uid)
        username = (req.get('username') if req else '') or ''
        chat_id = req.get('chat_id') if req else None
        chat_type = req.get('chat_type', 'private') if req else 'private'
        req_ws_id = req.get('workspace_id') if req else None

        target_ws_id = None
        # Provision or join workspace and membership
        if req_ws_id:
            ws = get_workspace_by_id(req_ws_id)
            if ws:
                target_ws_id = ws.id
                add_workspace_member(ws.id, target_uid, username=username, display_name=display_name, role=role)
        elif chat_id:
            ws = get_workspace_by_chat_id(chat_id)
            if not ws:
                ws = get_or_create_workspace(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    title=f"{display_name} (DM)",
                    creator_user_id=target_uid,
                    username=username,
                    display_name=display_name
                )
            target_ws_id = ws.id
            add_workspace_member(
                ws.id, target_uid,
                username=username,
                display_name=display_name,
                role=role
            )

        if not target_ws_id:
            target_ws_id = ws_id or get_default_workspace_id()

        # Update access request status to approved and set scoped role
        approver_id = getattr(query.from_user, 'id', 0)
        update_access_request_status(target_uid, 'approved', reviewed_by=approver_id)
        set_user_permission_and_role(target_uid, role, is_active=True, workspace_id=target_ws_id)

        # Audit log event
        approver_role = "owner" if is_owner(update) else "admin"
        log_audit_event(
            workspace_id=target_ws_id,
            actor_user_id=approver_id,
            actor_role=approver_role,
            action="access_request_approved",
            resource=f"user:{target_uid}",
            details={"role": role, "username": username}
        )

        try:
            await query.answer(f"✅ Approved as {role.title()}!")
        except Exception:
            pass

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        await safe_edit_callback_message(
            query,
            f"✅ <b>Access Approved!</b>\n\n"
            f"👤 <b>User:</b> {html.escape(display_name)}\n"
            f"💬 <b>Username:</b> @{html.escape(username) if username else 'N/A'}\n"
            f"🆔 <b>User ID:</b> <code>{target_uid}</code>\n"
            f"🛡️ <b>Role Granted:</b> <b>{role.upper()}</b>\n\n"
            f"<i>The user has been notified and granted access.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Manage Permissions", callback_data=f"perm_view:{target_uid}")],
                [InlineKeyboardButton("👥 View All Users", callback_data="perm_list")]
            ]),
            parse_mode='HTML'
        )

        # Notify approved user
        if chat_id:
            try:
                await context.bot.send_message(
                    chat_id=int(chat_id),
                    text=(
                        f"🎉 <b>Access Approved!</b>\n\n"
                        f"The administrator has approved your access with <b>{role.title()}</b> permissions.\n\n"
                        f"Send /start to begin tracking your expenses!"
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.warning(f"Could not notify approved user {target_uid}: {e}")
            return

    elif action == "auth_deny":
        if not is_owner(update):
            await query.answer("⛔ Only the bot owner can deny access.", show_alert=True)
            return
        target_uid = int(parts[1])
        from database.queries import get_access_request, update_access_request_status, set_user_permission_and_role
        from services.audit_service import log_audit_event

        req = get_access_request(target_uid)
        display_name = (req.get('display_name') if req else '') or str(target_uid)
        chat_id = req.get('chat_id') if req else None
        req_ws_id = (req.get('workspace_id') if req else None) or get_default_workspace_id()

        approver_id = getattr(query.from_user, 'id', 0)
        update_access_request_status(target_uid, 'rejected', reviewed_by=approver_id)
        set_user_permission_and_role(target_uid, 'viewer', is_active=False)

        approver_role = "owner" if is_owner(update) else "admin"
        log_audit_event(
            workspace_id=req_ws_id,
            actor_user_id=approver_id,
            actor_role=approver_role,
            action="access_request_denied",
            resource=f"user:{target_uid}"
        )

        try:
            await query.answer("❌ Access Denied & Blocked.")
        except Exception:
            pass

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        await safe_edit_callback_message(
            query,
            f"❌ <b>Access Request Denied</b>\n\n"
            f"👤 <b>User:</b> {html.escape(display_name)}\n"
            f"🆔 <b>User ID:</b> <code>{target_uid}</code>\n\n"
            f"<i>This user is blocked from interacting with the bot.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 View All Users", callback_data="perm_list")]
            ]),
            parse_mode='HTML'
        )

        if chat_id:
            try:
                await context.bot.send_message(
                    chat_id=int(chat_id),
                    text=(
                        "⛔ <b>Access Request Declined</b>\n\n"
                        "Your request to access Payment Tracker was declined by the administrator."
                    ),
                    parse_mode='HTML'
                )
            except Exception:
                pass
        return

    # --- Interactive Permissions & Roles Editor Callbacks ---
    elif action == "perm_list":
        if not is_owner(update):
            await query.answer("⛔ Only the bot owner can manage permissions.", show_alert=True)
            return
        from bot.commands import render_permissions_list_payload
        text, markup = render_permissions_list_payload()
        await safe_edit_callback_message(query, text, reply_markup=markup, parse_mode='HTML')
        return

    elif action == "perm_view":
        if not is_owner(update):
            await query.answer("⛔ Only the bot owner can manage permissions.", show_alert=True)
            return
        target_uid = int(parts[1])
        from bot.commands import render_user_permission_card
        text, markup = render_user_permission_card(target_uid)
        await safe_edit_callback_message(query, text, reply_markup=markup, parse_mode='HTML')
        return

    elif action == "perm_set":
        if not is_owner(update):
            await query.answer("⛔ Only the bot owner can manage permissions.", show_alert=True)
            return
        target_uid = int(parts[1])
        setting = parts[2].lower() if len(parts) > 2 else "member"
        import config
        owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
        if owner_id and target_uid == int(owner_id) and setting in ('viewer', 'revoke'):
            await query.answer("⛔ The workspace owner's access cannot be revoked.", show_alert=True)
            return
        if setting == 'owner':
            await query.answer("⛔ The owner role cannot be granted via callback.", show_alert=True)
            return
        if setting not in ('member', 'admin', 'viewer', 'revoke'):
            setting = 'member'
        from database.queries import set_user_permission_and_role
        target_ws_scope = ws_id
        if setting == "revoke":
            set_user_permission_and_role(target_uid, "viewer", is_active=False, workspace_id=target_ws_scope)
            try:
                await query.answer("🚫 User access revoked & blocked!", show_alert=False)
            except Exception:
                pass
        else:
            set_user_permission_and_role(target_uid, setting, is_active=True, workspace_id=target_ws_scope)
            try:
                await query.answer(f"✅ Role set to {setting.upper()}!", show_alert=False)
            except Exception:
                pass

        from bot.commands import render_user_permission_card
        text, markup = render_user_permission_card(target_uid)
        await safe_edit_callback_message(query, text, reply_markup=markup, parse_mode='HTML')
        return

    elif action == "perm_remove":
        if not is_owner(update):
            await query.answer("⛔ Only the owner can remove members.", show_alert=True)
            return
        target_uid = int(parts[1])
        import config
        owner_id = getattr(config, 'TELEGRAM_USER_ID', None)
        if owner_id and target_uid == int(owner_id):
            await query.answer("⛔ You cannot remove the workspace owner.", show_alert=True)
            return
        from database.queries import get_all_users_for_permissions
        users = get_all_users_for_permissions()
        target_user = next((u for u in users if u['telegram_user_id'] == target_uid), None)
        display = (target_user['display_name'] if target_user else None) or f"User {target_uid}"
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        confirm_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❌ Yes, Remove", callback_data=f"perm_remove_confirm:{target_uid}"),
                InlineKeyboardButton("Cancel", callback_data=f"perm_view:{target_uid}")
            ]
        ])
        await safe_edit_callback_message(
            query,
            f"⚠️ <b>Confirm Member Removal</b>\n\nAre you sure you want to remove <b>{html.escape(display)}</b> (<code>{target_uid}</code>) from this workspace?\n\nThey will lose access to records in this workspace.",
            reply_markup=confirm_markup,
            parse_mode='HTML'
        )
        return

    elif action == "perm_remove_confirm":
        if not is_owner(update):
            await query.answer("⛔ Only the owner can remove members.", show_alert=True)
            return
        target_uid = int(parts[1])
        from database.queries import remove_workspace_member
        target_ws_id = ws_id or get_default_workspace_id()
        success = remove_workspace_member(target_ws_id, target_uid)
        if success:
            await query.answer("✅ Member removed from workspace!", show_alert=True)
            from bot.commands import render_permissions_list_payload
            text, markup = render_permissions_list_payload()
            await safe_edit_callback_message(query, text, reply_markup=markup, parse_mode='HTML')
        else:
            await query.answer("❌ Failed to remove member.", show_alert=True)
        return

    # --- 2. Quick Undo & Quick Add Actions ---
    elif action == "undo_tx":
        tx_id = parse_int_arg(parts, 1)
        if tx_id is None:
            await query.answer("❌ Invalid button data.", show_alert=True)
            return
        from database.queries import can_user_modify_transaction
        caller_id = update.effective_user.id if update.effective_user else None
        caller_role = ws_ctx.role if ws_ctx else 'member'
        if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
            await query.answer("⛔ You can only undo payments that you recorded.", show_alert=True)
            return
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found or already removed.", reply_markup=get_back_to_menu_keyboard())
            return
        deleted = delete_transaction(tx_id, workspace_id=ws_id)
        if deleted:
            new_bal = recalculate_all_balances(workspace_id=ws_id)
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except Exception as bkp_err:
                logger.warning(f"Could not update local JSON backup on undo: {bkp_err}")
            await safe_edit_callback_message(
                query,
                f"↩️ <b>Transaction #{tx_id} Undone!</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"Payment reverted from your ledger.\n\n"
                f"💼 <b>Current Balance:</b> <b>{format_currency(new_bal)}</b>",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("❌ Nothing was saved: Transaction not found.", reply_markup=get_back_to_menu_keyboard())
        return

    elif action == "quick_add":
        try:
            amt = float(parse_decimal_amount(parts[1], allow_zero=False))
            payee = validate_name(parts[2], max_length=120)
        except ValueError as err:
            await query.edit_message_text(f"❌ Invalid quick entry: {err}")
            return
        cat = parts[3] if len(parts) > 3 else "Food & Dining"
        from database.models import Transaction
        now_dt = get_current_time_in_tz()
        tx = Transaction(
            transaction_type="SENT",
            amount=amt,
            person_name=payee,
            recipient_name=payee,
            category=cat,
            transaction_date=now_dt.date(),
            transaction_time=now_dt.strftime("%I:%M %p"),
            payment_app="One-Tap Quick Entry",
            workspace_id=ws_id,
            telegram_user_id=int(query.from_user.id) if (query.from_user and getattr(query.from_user, 'id', None)) else None
        )
        success = commit_transaction(tx)
        if success:
            await query.edit_message_text(
                f"✅ <b>Payment Saved! #{tx.id}</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"💸 <b>{format_currency(amt)}</b> → <b>{html.escape(payee)}</b>\n"
                f"🏷 {html.escape(cat)}   📅 Today, {now_dt.strftime('%I:%M %p')}\n"
                f"💼 <b>Balance:</b> <b>{format_currency(tx.balance_after)}</b>",
                reply_markup=get_quick_undo_keyboard(tx.id),
                parse_mode='HTML'
            )
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
        return

    # 1. OCR Confirmation / Cancellation (Legacy fallback)
    elif action in ("confirm_tx", "cancel_tx"):
        tx_id = parts[1]
        transaction = fetch_pending_transaction(tx_id, workspace_id=ws_id)
        
        if not transaction:
            await query.edit_message_text("❌ Transaction expired or no longer available.")
            return

        if ws_id:
            transaction.workspace_id = ws_id
        if query.from_user and getattr(query.from_user, 'id', None):
            transaction.telegram_user_id = int(query.from_user.id)
            
        if action == "confirm_tx":
            success = commit_transaction(transaction)
            if success:
                response = format_success_message(transaction)
                await query.edit_message_text(response, parse_mode='HTML')
                from services.task_manager import schedule_debounced_backup
                schedule_debounced_backup(context.bot)
            else:
                await query.edit_message_text("⚠️ Transaction already recorded.")
            pop_pending_transaction(tx_id, workspace_id=ws_id)
            
        elif action == "cancel_tx":
            await query.edit_message_text("❌ Transaction cancelled.")
            pop_pending_transaction(tx_id, workspace_id=ws_id)
            
    # 2. Transaction Selection for Edit / Delete
    elif action == "select_edit":
        tx_id = parse_int_arg(parts, 1)
        if tx_id is None:
            await query.answer("❌ Invalid button data.", show_alert=True)
            return
        from database.queries import can_user_modify_transaction
        caller_id = update.effective_user.id if update.effective_user else None
        caller_role = ws_ctx.role if ws_ctx else 'member'
        if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
            await query.answer("⛔ You can only edit payments that you recorded.", show_alert=True)
            return
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
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
        tx_id = parse_int_arg(parts, 1)
        if tx_id is None:
            await query.answer("❌ Invalid button data.", show_alert=True)
            return
        from database.queries import can_user_modify_transaction
        caller_id = update.effective_user.id if update.effective_user else None
        caller_role = ws_ctx.role if ws_ctx else 'member'
        if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
            await query.answer("⛔ You can only delete payments that you recorded.", show_alert=True)
            return
        await query.answer()
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not tx:
            from database.db import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM transactions WHERE id = ? AND workspace_id = ?", (tx_id, ws_id))
                raw_row = cursor.fetchone()
            if raw_row and raw_row['deleted_at']:
                cur_bal = get_balance_setting(workspace_id=ws_id)
                await query.edit_message_text(
                    f"✅ <b>Delete Confirmed!</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"ℹ️ <b>Transaction #{tx_id} is already deleted from your ledger.</b>\n\n"
                    f"💰 <b>Current Balance:</b> <b>{html.escape(format_currency(cur_bal))}</b>",
                    reply_markup=get_back_to_menu_keyboard(),
                    parse_mode='HTML'
                )
                return
            await query.edit_message_text(
                f"❌ <b>Transaction Not Found</b>\nTransaction #{tx_id} could not be found.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
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
        tx_id = parse_int_arg(parts, 2)
        if tx_id is None:
            await query.answer("❌ Invalid button data.", show_alert=True)
            return
        from database.queries import can_user_modify_transaction
        caller_id = update.effective_user.id if update.effective_user else None
        caller_role = ws_ctx.role if ws_ctx else 'member'
        if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
            await query.answer("⛔ You can only edit payments that you recorded.", show_alert=True)
            return
        
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
        
    elif action in ("edit_cancel", "select_edit_cancel"):
        await query.answer("❌ Editing cancelled.", show_alert=False)
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)
        await query.edit_message_text("❌ <b>Editing Cancelled</b>\n\nNo changes were made.", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')

    # 4. Delete Confirmation
    elif action == "delete_confirm":
        tx_id = parse_int_arg(parts, 1)
        if tx_id is None:
            await query.answer("❌ Invalid button data.", show_alert=True)
            return
        from database.queries import can_user_modify_transaction
        caller_id = update.effective_user.id if update.effective_user else None
        caller_role = ws_ctx.role if ws_ctx else 'member'
        if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
            await query.answer("⛔ You can only delete payments that you recorded.", show_alert=True)
            return
        await query.answer("✅ Delete Confirmed!", show_alert=False)
        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not tx:
            from database.db import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM transactions WHERE id = ? AND workspace_id = ?", (tx_id, ws_id))
                raw_row = cursor.fetchone()
            if raw_row and raw_row['deleted_at']:
                cur_bal = get_balance_setting(workspace_id=ws_id)
                await query.edit_message_text(
                    f"✅ <b>Delete Confirmed!</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"ℹ️ <b>Transaction #{tx_id} is already deleted from your ledger.</b>\n\n"
                    f"💰 <b>Current Balance:</b> <b>{html.escape(format_currency(cur_bal))}</b>",
                    reply_markup=get_back_to_menu_keyboard(),
                    parse_mode='HTML'
                )
                return
            await query.edit_message_text(
                f"❌ <b>Transaction Not Found</b>\nTransaction #{tx_id} could not be found.",
                reply_markup=get_back_to_menu_keyboard(),
                parse_mode='HTML'
            )
            return

        amt_str = format_currency(tx['amount'])
        person = tx['person_name'] or "Unknown"
        tx_type = tx.get('transaction_type', 'TRANSACTION')

        from services.undo_service import record_delete_action
        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        record_delete_action(tx, chat_id=chat_id, user_id=user_id)

        success = delete_transaction(tx_id, workspace_id=ws_id)
        if success:
            new_bal = recalculate_all_balances(workspace_id=ws_id)
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except Exception as bkp_err:
                logger.warning(f"Could not update local JSON backup on delete: {bkp_err}")

            if is_gdrive_available():
                from config import DATA_DIR
                from services.task_manager import create_tracked_task
                bkp = DATA_DIR / 'backup_transactions.json'
                if os.path.exists(bkp):
                    create_tracked_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)), name="gdrive_backup_upload")
            
            await safe_edit_callback_message(
                query,
                f"✅ <b>Delete Confirmed!</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"🗑️ <b>Transaction #{tx_id} has been deleted.</b>\n\n"
                f"• <b>Type:</b> {html.escape(str(tx_type))}\n"
                f"• <b>Amount:</b> {html.escape(amt_str)}\n"
                f"• <b>Person:</b> {html.escape(str(person))}\n\n"
                f"💰 <b>Updated Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>",
                reply_markup=get_delete_confirmed_keyboard(),
                parse_mode='HTML'
            )
        else:
            await safe_edit_callback_message(query, "❌ Nothing was saved: Failed to delete transaction.", reply_markup=get_back_to_menu_keyboard())

    elif action in ("delete_cancel", "select_delete_cancel"):
        await query.answer("❌ Deletion cancelled.", show_alert=False)
        context.user_data.pop('action', None)
        await safe_edit_callback_message(
            query,
            "❌ <b>Deletion Cancelled</b>\n\nNo changes were made to your ledger.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )

    elif action == "undo_action":
        await query.answer()
        from services.undo_service import perform_undo
        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        success, msg = perform_undo(chat_id=chat_id, user_id=user_id, workspace_id=ws_id)
        if success:
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except Exception as bkp_err:
                logger.warning(f"Could not update local JSON backup on undo: {bkp_err}")
            await safe_edit_callback_message(query, msg, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        else:
            await safe_edit_callback_message(query, f"❌ Nothing was saved: {msg}", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')

    elif action == "undo_confirm":
        try:
            ans = query.answer("↩️ Processing Undo...", show_alert=False)
            if asyncio.iscoroutine(ans):
                await ans
        except Exception:
            pass
        from services.undo_service import perform_undo
        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        success, msg = await asyncio.to_thread(perform_undo, chat_id=chat_id, user_id=user_id, workspace_id=ws_id)
        if success:
            bal = get_balance_setting(workspace_id=ws_id)
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except Exception as bkp_err:
                logger.warning(f"Could not update local JSON backup on undo: {bkp_err}")
            text = (
                "✅ <b>Undo Confirmed & Applied!</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"{msg}\n\n"
                f"💰 <b>Current Balance:</b> <b>{html.escape(format_currency(bal))}</b>"
            )
            await safe_edit_callback_message(query, text, reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        else:
            await safe_edit_callback_message(query, f"❌ <b>Undo Failed:</b> {msg}", reply_markup=get_back_to_menu_keyboard(), parse_mode='HTML')
        return

    elif action == "undo_cancel":
        try:
            ans = query.answer("Undo cancelled.", show_alert=False)
            if asyncio.iscoroutine(ans):
                await ans
        except Exception:
            pass
        await query.edit_message_text(
            "❌ <b>Undo Cancelled</b>\n━━━━━━━━━━━━━━\nNo changes were made to your ledger.",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode='HTML'
        )
        return


    # 4b. Correct Amount for Existing Transaction
    elif action == "correct_amount":
        try:
            tx_id = int(parts[1])
            new_amt = float(parse_decimal_amount(parts[2], allow_zero=False))
        except (IndexError, ValueError):
            await query.answer("❌ Invalid amount.", show_alert=True)
            return

        caller_id = get_effective_user_id(update)
        caller_role = ws_ctx.role if ws_ctx else 'member'
        from database.queries import can_user_modify_transaction
        if not can_user_modify_transaction(tx_id, caller_id, caller_role, workspace_id=ws_id):
            await query.answer("⛔ You can only edit payments that you recorded.", show_alert=True)
            return

        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if not tx:
            await query.edit_message_text("❌ Transaction not found.")
            return
        success = update_transaction(tx_id, {'amount': new_amt}, workspace_id=ws_id)
        if success:
            new_bal = recalculate_all_balances(workspace_id=ws_id)
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            if is_gdrive_available():
                from config import DATA_DIR
                from services.task_manager import create_tracked_task
                bkp = DATA_DIR / 'backup_transactions.json'
                if os.path.exists(bkp):
                    create_tracked_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)), name="gdrive_backup_upload")
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
        if not query.message or not getattr(query.message, 'chat', None):
            try:
                await query.answer("❌ Message no longer accessible.", show_alert=True)
            except Exception:
                pass
            return
        fmt = parts[1]
        if fmt == "pdf":
            from bot.commands import send_pdf_report
            await query.edit_message_text("⏳ Generating official PDF statement...")
            await send_pdf_report(query.message.chat, context.bot, workspace_id=ws_id)
        elif fmt == "excel":
            from bot.commands import send_excel_report
            await query.edit_message_text("⏳ Generating Excel spreadsheet...")
            await send_excel_report(query.message.chat, context.bot, workspace_id=ws_id)

    # 5. Interactive Filter Callbacks
    elif action == "filter":
        filter_type = parts[1]
        now = get_current_time_in_tz()
        
        if filter_type == "today":
            txs = search_transactions(target_date=now.date(), sort_by="date_desc", workspace_id=ws_id)
            title = f"📅 Today's Transactions ({now.strftime('%d %b %Y')})"
        elif filter_type == "yesterday":
            y_date = now.date() - timedelta(days=1)
            txs = search_transactions(target_date=y_date, sort_by="date_desc", workspace_id=ws_id)
            title = f"📅 Yesterday's Transactions ({y_date.strftime('%d %b %Y')})"
        elif filter_type == "this_month":
            txs = search_transactions(year=now.year, month=now.month, sort_by="date_desc", workspace_id=ws_id)
            title = f"🗓️ This Month's Transactions ({now.strftime('%B %Y')})"
        elif filter_type == "monthly_stats":
            stats = get_monthly_summary(now.year, now.month, workspace_id=ws_id)
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
            txs = search_transactions(tx_type="SENT", limit=15, sort_by="date_desc", workspace_id=ws_id)
            title = "🔴 Recent Sent Transactions"
        elif filter_type == "type_received":
            txs = search_transactions(tx_type="RECEIVED", limit=15, sort_by="date_desc", workspace_id=ws_id)
            title = "🟢 Recent Received Transactions"
        elif filter_type == "show_ids":
            txs = get_recent_transactions(limit=10, workspace_id=ws_id)
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
        txs = search_transactions(sort_by=sort_by, limit=10, workspace_id=ws_id)
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
    elif action in CAFE_DISPATCH_TABLE:
        from bot.callbacks import handle_cafe_callback
        await handle_cafe_callback(query, context, action, parts, ws_id, ws_ctx, update)
        return


    # --- 13. Gemini Model Switching & Refresh ---
    elif action == "set_model":
        target_model = parts[1] if len(parts) > 1 else "AUTO"
        from database.queries import set_model_setting
        from bot.commands import render_gemini_status_payload

        valid_models = {"AUTO", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"}
        if target_model not in valid_models:
            target_model = "AUTO"

        set_model_setting(target_model)
        toast_label = "Auto-Failover (3.8 -> 3.7 -> 3.6 -> 3.5)" if target_model == "AUTO" else target_model
        try:
            await query.answer(f"✅ Priority set to {toast_label}!", show_alert=False)
        except Exception:
            pass

        card, keyboard = await render_gemini_status_payload(force_refresh=False)
        try:
            await query.edit_message_text(card, reply_markup=keyboard, parse_mode='HTML')
        except Exception:
            pass
        return

    elif action == "refresh_gemini":
        from bot.commands import render_gemini_status_payload
        try:
            await query.answer("🔄 Refreshing Gemini quota and status…")
        except Exception:
            pass
        card, keyboard = await render_gemini_status_payload(force_refresh=True)
        try:
            await query.edit_message_text(card, reply_markup=keyboard, parse_mode='HTML')
        except Exception:
            pass
        return


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button presses from inline keyboards with central authorization policy and logs errors."""
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await _dispatch_callback_query(update, context)
    except BadRequest as e:
        logger.warning("BadRequest handling callback query data '%s': %s", query.data, e)
        try:
            await query.answer("❌ Request could not be processed.", show_alert=True)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Error handling callback query data '%s': %s", query.data, e, exc_info=True)
        try:
            await query.answer("❌ An error occurred.", show_alert=True)
        except Exception:
            pass
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
    if not await require_authorized(update): return
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    message_id = str(update.message.message_id)

    ws_ctx = get_workspace_context(update)
    ws_id = ws_ctx.workspace_id if ws_ctx else None
    
    # Normalize commands e.g. \\date, /search, /monthly, /filter, /sort, /details, /undo
    from bot.commands import (
        edit_command, delete_command, history_command, balance_command,
        date_command, search_command, monthly_command, filter_command, sort_command, details_command, amount_command,
        undo_command, workspace_command, members_command, setrole_command, permissions_command
    )
    cmd_lower = text.lower()
    cmd_tokens = cmd_lower.split()
    cmd_token = cmd_tokens[0] if cmd_tokens else ""
    
    if cmd_token in (r'\workspace', 'workspace', '/workspace', r'\workspaces', 'workspaces', '/workspaces'):
        parts = text.split(maxsplit=1)
        context.args = parts[1].split() if len(parts) > 1 else []
        await workspace_command(update, context)
        return
    elif cmd_lower in (r'\permissions', 'permissions', '/permissions', r'\roles', 'roles', '/roles', r'\users', 'users', '/users'):
        await permissions_command(update, context)
        return
    elif cmd_lower in (r'\members', 'members', '/members', r'\team', 'team', '/team'):
        await members_command(update, context)
        return
    elif cmd_token in (r'\setrole', 'setrole', '/setrole'):
        parts = text.split(maxsplit=1)
        context.args = parts[1].split() if len(parts) > 1 else []
        await setrole_command(update, context)
        return
    elif cmd_token in (r'\admin', 'admin', '/admin', r'\makeadmin', 'makeadmin', '/makeadmin', r'\promote', 'promote', '/promote'):
        from bot.commands import admin_command
        parts = text.split(maxsplit=1)
        context.args = parts[1].split() if len(parts) > 1 else []
        await admin_command(update, context)
        return
    elif cmd_lower in (r'\undo', 'undo', '/undo', 'revert', '/revert', r'\revert'):
        await undo_command(update, context)
        return
    elif cmd_lower in (r'\backup', 'backup', '/backup', r'\backupnow', 'backupnow', '/backupnow'):
        from bot.commands import backup_command
        await backup_command(update, context)
        return
    elif cmd_token in (r'\delete', 'delete', '/delete'):
        parts = text.split(maxsplit=1)
        context.args = parts[1].split() if len(parts) > 1 else []
        await delete_command(update, context)
        return
    elif cmd_token in (r'\edit', 'edit', '/edit'):
        parts = text.split(maxsplit=1)
        context.args = parts[1].split() if len(parts) > 1 else []
        await edit_command(update, context)
        return
    elif cmd_lower in (r'\history', 'history', '/history'):
        await history_command(update, context)
        return
    elif cmd_lower in (r'\balance', 'balance', '/balance'):
        await balance_command(update, context)
        return
    elif cmd_token in (r'\date', 'date', '/date'):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await date_command(update, context)
        return
    elif cmd_token in (r'\search', 'search', '/search'):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await search_command(update, context)
        return
    elif cmd_token in (r'\amount', 'amount', '/amount'):
        parts = text.split(maxsplit=1)
        context.args = [parts[1]] if len(parts) > 1 else []
        await amount_command(update, context)
        return
    elif cmd_lower in (r'\monthly', 'monthly', '/monthly', 'stats', '/stats'):
        await monthly_command(update, context)
        return
    elif cmd_lower in (r'\filter', 'filter', '/filter'):
        await filter_command(update, context)
        return
    elif cmd_lower in (r'\sort', 'sort', '/sort'):
        await sort_command(update, context)
        return
    elif cmd_lower in (r'\details', 'details', '/details', 'ids', '/ids'):
        await details_command(update, context)
        return
    elif cmd_lower in (r'\menu', 'menu', '/menu', 'cafeteria', '/cafeteria', 'canteen', '/canteen'):
        from bot.commands import menu_command
        await menu_command(update, context)
        return
    elif cmd_lower in (r'\dashboard', 'dashboard', '/dashboard'):
        from bot.commands import dashboard_command
        await dashboard_command(update, context)
        return
    elif cmd_lower in (r'\gemini', 'gemini', '/gemini', r'\geministatus', 'geministatus', '/geministatus', r'\quota', 'quota', '/quota', r'\ai', 'ai', '/ai', r'\status', 'status', '/status'):
        from bot.commands import geministatus_command
        await geministatus_command(update, context)
        return
    elif cmd_token in (r'\setmodel', 'setmodel', '/setmodel', r'\model', 'model', '/model'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import setmodel_command
        await setmodel_command(update, context)
        return
    elif cmd_token in (r'\digest', 'digest', '/digest'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import digest_command
        await digest_command(update, context)
        return
    elif cmd_token in (r'\insights', 'insights', '/insights'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import insights_command
        await insights_command(update, context)
        return
    elif cmd_token in (r'\budget', 'budget', '/budget'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import budget_command
        await budget_command(update, context)
        return
    elif cmd_token in (r'\setbudget', 'setbudget', '/setbudget'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import setbudget_command
        await setbudget_command(update, context)
        return
    elif cmd_token in (r'\setbalance', 'setbalance', '/setbalance'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import setbalance_command
        await setbalance_command(update, context)
        return
    elif cmd_token in (r'\export', 'export', '/export', 'statement', '/statement', r'\statement', 'report', '/report', r'\report'):
        parts = text.split(maxsplit=1)
        context.args = parts[1:] if len(parts) > 1 else []
        from bot.commands import export_command
        await export_command(update, context)
        return
    elif cmd_lower in (r'\help', 'help', '/help'):
        from bot.commands import help_command
        await help_command(update, context)
        return
    elif cmd_lower in (r'\cafestats', 'cafestats', '/cafestats', 'cafespends', '/cafespends'):
        from bot.commands import cafestats_command
        await cafestats_command(update, context)
        return
    elif cmd_token in (r'\cafeedit', 'cafeedit', '/cafeedit', 'editcafe', '/editcafe', r'\editcafe'):
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
            txs = search_transactions(exact_amount=val, workspace_id=ws_id)
            if txs:
                context.args = [clean_num]
                await amount_command(update, context)
                return

    # Check if user is in an interactive state
    pending_action = context.user_data.get('action')
    if pending_action:
        member_actions = {
            'waiting_edit_id', 'waiting_delete_id', 'waiting_edit_value',
            'waiting_edit_pending_value', 'waiting_payee_amount',
            'waiting_quick_text', 'waiting_cafe_custom_amount'
        }
        if pending_action in member_actions:
            if not await require_member(update):
                return
        else:
            if not await require_admin(update):
                return
    
    if pending_action == 'waiting_edit_id':
        clean_id_str = text.replace('#', '').strip()
        if clean_id_str.isdigit():
            num = int(clean_id_str)
            recent_ids = context.user_data.get('recent_edit_ids') or [t['id'] for t in get_recent_transactions(limit=10, workspace_id=ws_id)]
            tx = None
            if num in recent_ids:
                tx = get_transaction_by_id(num, workspace_id=ws_id)
            elif 1 <= num <= len(recent_ids):
                tx = get_transaction_by_id(recent_ids[num - 1], workspace_id=ws_id)
            if not tx:
                tx = get_transaction_by_id(num, workspace_id=ws_id)
            if tx:
                caller_id = update.effective_user.id if update.effective_user else None
                caller_role = getattr(context, 'user_role', None) or (ws_ctx.role if ws_ctx else 'member')
                from database.queries import can_user_modify_transaction
                if not can_user_modify_transaction(tx['id'], caller_id, caller_role) and not is_admin_user(update):
                    await update.message.reply_text("⛔ You can only edit payments that you recorded.")
                    return
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
            recent_ids = context.user_data.get('recent_delete_ids') or [t['id'] for t in get_recent_transactions(limit=10, workspace_id=ws_id)]
            tx = None
            if num in recent_ids:
                tx = get_transaction_by_id(num, workspace_id=ws_id)
            elif 1 <= num <= len(recent_ids):
                tx = get_transaction_by_id(recent_ids[num - 1], workspace_id=ws_id)
            if not tx:
                tx = get_transaction_by_id(num, workspace_id=ws_id)
            if tx:
                caller_id = update.effective_user.id if update.effective_user else None
                caller_role = getattr(context, 'user_role', None) or (ws_ctx.role if ws_ctx else 'member')
                from database.queries import can_user_modify_transaction
                if not can_user_modify_transaction(tx['id'], caller_id, caller_role) and not is_admin_user(update):
                    await update.message.reply_text("⛔ You can only delete payments that you recorded.")
                    return
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
        caller_id = update.effective_user.id if update.effective_user else None
        caller_role = getattr(context, 'user_role', None) or (ws_ctx.role if ws_ctx else 'member')
        from database.queries import can_user_modify_transaction
        if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
            context.user_data.pop('action', None)
            context.user_data.pop('edit_tx_id', None)
            context.user_data.pop('edit_field', None)
            await update.message.reply_text("⛔ You can only edit payments that you recorded.")
            return
        field = context.user_data.get('edit_field')

        updates = {}
        needs_recalc = False

        if field == 'amount':
            try:
                from utils.validation import parse_decimal_amount
                new_amt = float(parse_decimal_amount(text, allow_zero=False))
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid amount: {err}. Please enter a valid positive number:")
                return
            updates['amount'] = new_amt
            needs_recalc = True
        elif field == 'person':
            from utils.validation import validate_name
            try:
                clean_name = validate_name(text.title(), max_length=120, field_name="Person name", required=True)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid name: {err}")
                return
            updates['person_name'] = clean_name
            tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
            if tx and tx['transaction_type'] == 'SENT':
                updates['recipient_name'] = clean_name
            else:
                updates['sender_name'] = clean_name
        elif field == 'type':
            try:
                from utils.validation import validate_transaction_type
                new_type = validate_transaction_type(text)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid type: {err}. Please enter <code>SENT</code> or <code>RECEIVED</code>:", parse_mode='HTML')
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
            from utils.validation import validate_reference
            try:
                updates['reference_number'] = validate_reference(text, max_length=100)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid reference: {err}")
                return

        tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
        if tx:
            from services.undo_service import record_edit_action
            record_edit_action(tx, chat_id=update.effective_chat.id if update.effective_chat else None, user_id=caller_id, workspace_id=ws_id)

        success = update_transaction(tx_id, updates, workspace_id=ws_id)
        context.user_data.pop('action', None)
        context.user_data.pop('edit_tx_id', None)
        context.user_data.pop('edit_field', None)

        if success:
            if needs_recalc:
                new_bal = recalculate_all_balances(workspace_id=ws_id)
            else:
                new_bal = get_balance_setting(workspace_id=ws_id)

            updated_tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
            person = (updated_tx['person_name'] if updated_tx else '') or "Unknown"
            amt_s = format_currency(updated_tx['amount']) if updated_tx else ''
            bal_flow = ""
            if updated_tx and 'balance_before' in updated_tx and 'balance_after' in updated_tx:
                bal_flow = f"\n💰 <b>Balance Flow:</b> {html.escape(format_currency(updated_tx['balance_before']))} ➔ <b>{html.escape(format_currency(updated_tx['balance_after']))}</b>"

            from bot.keyboards import get_undo_keyboard
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            if is_gdrive_available():
                from config import DATA_DIR
                from services.task_manager import create_tracked_task
                bkp = DATA_DIR / 'backup_transactions.json'
                if os.path.exists(bkp):
                    create_tracked_task(asyncio.to_thread(upload_backup_to_drive, str(bkp)), name="gdrive_backup_upload")
            await update.message.reply_text(
                f"✅ <b>Transaction #{tx_id} Updated</b>\n\n"
                f"👤 <b>Person:</b> {html.escape(str(person))}\n"
                f"💵 <b>Amount:</b> <b>{html.escape(amt_s)}</b>{bal_flow}\n\n"
                f"💳 <b>Current Balance:</b> <b>{html.escape(format_currency(new_bal))}</b>",
                reply_markup=get_undo_keyboard(),
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
        
        try:
            from utils.validation import parse_decimal_amount
            parsed_amt = float(parse_decimal_amount(text, allow_zero=False))
        except ValueError:
            parsed_amt = 0.0
        custom_note = text.strip()
        
        if tx_id:
            caller_id = update.effective_user.id if update.effective_user else None
            caller_role = getattr(context, 'user_role', None) or (ws_ctx.role if ws_ctx else 'member')
            from database.queries import can_user_modify_transaction
            if not can_user_modify_transaction(tx_id, caller_id, caller_role) and not is_admin_user(update):
                await update.message.reply_text("⛔ You can only edit payments that you recorded.")
                return
            tx = get_transaction_by_id(tx_id, workspace_id=ws_id)
            if tx:
                if parsed_amt > 0:
                    tag_desc = f"{item_type} (₹{parsed_amt:.0f})" if item_type != 'Custom' else f"Custom Item (₹{parsed_amt:.0f})"
                else:
                    tag_desc = f"{item_type}: {custom_note}"
                    
                new_name = f"VIKRAMAN NAIR K (Cafeteria: {tag_desc})"
                update_transaction(tx_id, {'person_name': new_name, 'category': 'Food & Dining'}, workspace_id=ws_id)
                from services.task_manager import schedule_debounced_backup
                schedule_debounced_backup(context.bot)
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
        from utils.validation import parse_decimal_amount
        full_arg = text.strip()
        name, price, category = None, None, "Custom"
        if "," in full_arg:
            parts = [p.strip() for p in full_arg.split(",")]
            name = parts[0]
            if len(parts) > 1:
                try:
                    price = float(parse_decimal_amount(parts[1], allow_zero=False))
                except ValueError:
                    price = None
            if len(parts) > 2:
                category = parts[2]
        else:
            tokens = full_arg.split()
            price_idx = -1
            for idx, tok in enumerate(tokens):
                try:
                    p_val = float(parse_decimal_amount(tok, allow_zero=False))
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

        success, msg = add_custom_menu_item(name, price, category, is_veg=True, workspace_id=ws_id)
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
        success, msg = delete_custom_menu_item(text.strip(), workspace_id=ws_id)
        if success:
            await update.message.reply_text(f"✅ {html.escape(msg)}", parse_mode='HTML')
        else:
            await update.message.reply_text(f"❌ {html.escape(msg)}", parse_mode='HTML')
        return

    elif pending_action == 'waiting_edit_pending_value':
        pending_id = context.user_data.pop('pending_id', None)
        field = context.user_data.pop('pending_field', None)
        context.user_data.pop('action', None)

        allowed_fields = {'amount', 'person', 'category', 'date', 'type'}
        if not field or field not in allowed_fields:
            await update.message.reply_text("❌ Invalid field being edited.", reply_markup=get_home_menu_keyboard(), parse_mode='HTML')
            return

        transaction = fetch_pending_transaction(pending_id, workspace_id=ws_id)
        if not transaction:
            await update.message.reply_text("❌ Receipt has expired.", reply_markup=get_home_menu_keyboard(), parse_mode='HTML')
            return

        if field == 'amount':
            try:
                from utils.validation import parse_decimal_amount
                val = float(parse_decimal_amount(text, allow_zero=False))
                transaction.amount = val
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid amount: {err}. Please send a positive number:")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
        elif field == 'person':
            from utils.validation import validate_name
            try:
                p_name = validate_name(text.strip(), max_length=120, field_name="Person name", required=True)
                transaction.person_name = p_name
                if transaction.transaction_type == 'SENT':
                    transaction.recipient_name = p_name
                else:
                    transaction.sender_name = p_name
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid name: {err}. Please enter a valid name:")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
        elif field == 'type':
            try:
                from utils.validation import validate_transaction_type
                transaction.transaction_type = validate_transaction_type(text)
            except ValueError as err:
                await update.message.reply_text(f"❌ Invalid type: {err}. Must be SENT or RECEIVED:")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
        elif field == 'category':
            from services.category_service import CATEGORIES
            allowed_cats = set(CATEGORIES.keys()) | {
                "Transport", "Healthcare", "Salary", "General", "Income", "Investment", "Personal"
            }
            clean_cat = text.strip()
            matched = next((c for c in allowed_cats if c.lower() == clean_cat.lower()), None)
            if not matched:
                await update.message.reply_text("❌ Invalid category. Please enter a valid category (e.g. Food & Dining, Groceries, Shopping, General):")
                context.user_data['pending_id'] = pending_id
                context.user_data['pending_field'] = field
                context.user_data['action'] = 'waiting_edit_pending_value'
                return
            transaction.category = matched
        elif field == 'date':
            d_val = parse_date(text)
            if d_val:
                transaction.transaction_date = d_val

        set_pending_transaction(pending_id, transaction, workspace_id=ws_id)

        dup = find_potential_duplicate(
            amount=transaction.amount,
            reference_number=transaction.reference_number,
            person_name=transaction.person_name,
            tx_date=str(transaction.transaction_date) if transaction.transaction_date else None,
            workspace_id=ws_id
        )
        dup_warning = f"⚠️ Similar to #{dup['id']} ({dup.get('match_reason', 'duplicate')}) — duplicate?" if dup else None
        card_text = format_receipt_card(transaction, dup_warning)
        await update.message.reply_text(
            f"✅ <b>Field Updated!</b>\n\n{card_text}",
            reply_markup=get_confirmation_card_keyboard(pending_id, duplicate_warning=bool(dup)),
            parse_mode='HTML'
        )
        return

    elif pending_action == 'waiting_payee_amount':
        payee = context.user_data.pop('quick_payee', 'Payee')
        tt = context.user_data.pop('quick_type', 'SENT')
        context.user_data.pop('action', None)
        try:
            from utils.validation import parse_decimal_amount
            val = float(parse_decimal_amount(text.strip(), allow_zero=False))
        except Exception as err:
            await update.message.reply_text(f"❌ Invalid amount: {err}. Please try again:")
            return

        cat = get_payee_category(payee, ws_id) or "General"
        from database.models import Transaction
        t = Transaction(
            amount=val,
            transaction_type=tt,
            person_name=payee,
            category=cat,
            workspace_id=ws_id,
            telegram_user_id=update.effective_user.id if update.effective_user else None
        )
        pid = uuid.uuid4().hex
        set_pending_transaction(pid, t, workspace_id=ws_id)
        card_text = format_receipt_card(t)
        await update.message.reply_text(
            card_text,
            reply_markup=get_confirmation_card_keyboard(pid),
            parse_mode='HTML'
        )
        return

    elif pending_action == 'waiting_quick_text':
        context.user_data.pop('action', None)
        # Process natural text through process_transaction
        transaction, conf = process_transaction(text, "", "", "")
        if transaction and transaction.amount and transaction.amount > 0:
            if ws_id:
                transaction.workspace_id = ws_id
            if update.effective_user:
                transaction.telegram_user_id = update.effective_user.id
            pid = uuid.uuid4().hex
            set_pending_transaction(pid, transaction, workspace_id=ws_id)
            card_text = format_receipt_card(transaction)
            await update.message.reply_text(
                card_text,
                reply_markup=get_confirmation_card_keyboard(pid),
                parse_mode='HTML'
            )
            return

    elif pending_action == 'waiting_rec_add':
        context.user_data.pop('action', None)
        raw_parts = [p.strip() for p in text.split(",")]
        if len(raw_parts) < 2:
            await update.message.reply_text("❌ Please provide at least Payee and Amount separated by comma (e.g. <code>Netflix, 649, Monthly</code>).", parse_mode='HTML')
            return
        payee = raw_parts[0]
        try:
            from utils.validation import parse_decimal_amount
            amt = float(parse_decimal_amount(raw_parts[1], allow_zero=False))
        except Exception as err:
            await update.message.reply_text(f"❌ Invalid amount: {err}")
            return
        freq = raw_parts[2].upper() if len(raw_parts) > 2 and raw_parts[2].upper() in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY') else 'MONTHLY'
        from services.recurring_service import add_recurring_payment, get_upcoming_recurring
        from bot.commands import render_recurring_overview_text
        from bot.keyboards import get_recurring_menu_keyboard
        rec_id = add_recurring_payment(payee, amt, frequency=freq, workspace_id=ws_id)
        upcoming = get_upcoming_recurring(30, workspace_id=ws_id)
        caller_role = ws_ctx.role if ws_ctx else 'viewer'
        await update.message.reply_text(
            f"✅ <b>Recurring Payment #{rec_id} Created!</b>\n"
            f"• <b>Payee:</b> {html.escape(payee)}\n"
            f"• <b>Amount:</b> {format_currency(amt)}\n"
            f"• <b>Frequency:</b> {freq.capitalize()}\n\n"
            f"{render_recurring_overview_text(workspace_id=ws_id)}",
            reply_markup=get_recurring_menu_keyboard(upcoming_items=upcoming, role=caller_role),
            parse_mode='HTML'
        )
        return

    # Check quick menu trigger
    if text.strip().lower() in ('menu', 'home', 'start'):
        caller_id = update.effective_user.id if update.effective_user else None
        view_user_id = None if (ws_ctx and ws_ctx.role in ('owner', 'admin')) else caller_id
        await update.message.reply_text(render_home_menu_text(workspace_id=ws_id, user_id=view_user_id), reply_markup=get_home_menu_keyboard(), parse_mode='HTML')
        return

    # Check short text entry: e.g. "120 dosa", "+500 salary", "-45 tea", "coffee 15"
    short_parsed = parse_short_entry(text)
    if short_parsed:
        if not await require_member(update):
            return
        amt, tx_type, name = short_parsed
        from database.models import Transaction
        now_dt = get_current_time_in_tz()
        
        # Category heuristics & payee memory
        cat = get_payee_category(name, ws_id)
        if not cat:
            lower_name = name.lower()
            if any(w in lower_name for w in ('dosa', 'tea', 'coffee', 'meals', 'lunch', 'canteen', 'cafe', 'food', 'snack', 'breakfast', 'dinner', 'vada', 'poori', 'chapathi', 'juice', 'ice cream')):
                cat = "Food & Dining"
            elif any(w in lower_name for w in ('salary', 'dividend', 'bonus', 'freelance', 'interest')):
                cat = "Salary"
            elif any(w in lower_name for w in ('auto', 'uber', 'ola', 'metro', 'bus', 'fuel', 'petrol')):
                cat = "Transport"
            elif any(w in lower_name for w in ('grocery', 'groceries', 'milk', 'vegetables', 'fruits')):
                cat = "Groceries"
            else:
                cat = "General"

        tx = Transaction(
            transaction_type=tx_type,
            amount=amt,
            person_name=name.title(),
            category=cat,
            transaction_date=now_dt.date(),
            transaction_time=now_dt.strftime("%I:%M %p"),
            payment_app="Short Text Entry",
            workspace_id=ws_id,
            telegram_user_id=update.effective_user.id if update.effective_user else None
        )
        if tx_type == 'SENT':
            tx.recipient_name = name.title()
        else:
            tx.sender_name = name.title()

        success = commit_transaction(tx)
        if success:
            arrow = "←" if tx_type == "RECEIVED" else "→"
            await update.message.reply_text(
                f"✅ <b>Payment Saved! #{tx.id}</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"💸 <b>{format_currency(amt)}</b> {arrow} <b>{html.escape(name.title())}</b>\n"
                f"🏷 {html.escape(cat)}   📅 Today, {now_dt.strftime('%I:%M %p')}\n"
                f"💼 <b>Balance:</b> <b>{format_currency(tx.balance_after)}</b>",
                reply_markup=get_quick_undo_keyboard(tx.id),
                parse_mode='HTML'
            )
            from services.task_manager import schedule_debounced_backup
            schedule_debounced_backup(context.bot)
            return

    # Try parsing text as a transaction
    try:
        transaction, confidence = process_transaction(text, "", message_id, chat_id)
        if transaction.amount and transaction.amount > 0 and transaction.transaction_type:
            if not await require_member(update):
                return
            if ws_id:
                transaction.workspace_id = ws_id
            if update.effective_user:
                transaction.telegram_user_id = update.effective_user.id
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
                    from services.task_manager import schedule_debounced_backup
                    schedule_debounced_backup(context.bot)
                else:
                    await update.message.reply_text("⚠️ Transaction already recorded.")
                return
            elif confidence >= 30:
                tx_id = uuid.uuid4().hex
                set_pending_transaction(tx_id, transaction, workspace_id=ws_id)
                card_text = format_receipt_card(transaction)
                await update.message.reply_text(card_text, reply_markup=get_confirmation_card_keyboard(tx_id), parse_mode='HTML')
                return
    except Exception as e:
        logger.error(f"Error parsing text message: {e}", exc_info=True)
    
    # Clean app menu response if message wasn't recognized
    await update.message.reply_text(
        "👋 <b>Payment Tracker Bot</b>\n\n"
        "• Send a <b>screenshot</b> of any payment receipt.\n"
        "• Type a short entry like <code>120 dosa</code> or <code>+500 salary</code>.\n"
        "• Or tap <b>Menu</b> below to open the interactive app:",
        reply_markup=get_home_menu_keyboard(),
        parse_mode='HTML'
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles uploaded backup JSON documents.
    Flow: download → validate → preview → owner confirmation → import → recalculate
          → verify → export fresh backup → cloud backup.
    The uploaded file is NEVER written to BACKUP_JSON_PATH; it is treated as a
    temporary input only. After a successful import a fresh export is created.
    """
    if not update.message or not update.message.document:
        return
    doc = update.message.document
    fn = (doc.file_name or "").lower()
    if not fn.endswith('.json'):
        return

    from bot.auth import require_owner
    if not await require_owner(update):
        return

    from config import BACKUP_JSON_PATH
    from services.backup_service import preview_database_import

    # Use a UUID-named temp file — never overwrite BACKUP_JSON_PATH
    tmp_token = uuid.uuid4().hex[:12]
    data_dir = BACKUP_JSON_PATH.parent
    tmp_path = data_dir / f"tmp_upload_{tmp_token}.json"

    await update.message.reply_text(
        "📥 <b>Backup file received. Downloading and validating…</b>",
        parse_mode='HTML'
    )
    try:
        file_obj = await context.bot.get_file(doc.file_id)
        await file_obj.download_to_drive(custom_path=tmp_path)

        # -- Validate & preview (dry-run, no DB writes) --
        preview = await asyncio.to_thread(preview_database_import, tmp_path)
        if not preview.get('success'):
            await update.message.reply_text(
                f"❌ <b>Validation failed:</b> {html.escape(str(preview.get('error', 'Unknown error')))}\n\n"
                "The file has not been imported. No changes were made.",
                parse_mode='HTML'
            )
            return

        # Prune expired staged imports (older than 10 minutes)
        now_ts = time.time()
        _prune_pending_json_imports()

        from bot.auth import get_workspace_context
        ws_ctx = get_workspace_context(update)
        ws_id = ws_ctx.workspace_id if ws_ctx else get_default_workspace_id()
        uploader_id = update.effective_user.id if update.effective_user else None

        # -- Stash validated path for confirmation step --
        _pending_json_imports[tmp_token] = {
            'path': tmp_path,
            'preview': preview,
            'created_at': now_ts,
            'uploader_id': uploader_id,
            'workspace_id': ws_id,
        }

        rev = preview.get('revision', '?')
        exported_at = preview.get('exported_at', '?')
        to_add = preview.get('to_add', 0)
        to_update = preview.get('to_update', 0)
        to_skip = preview.get('to_skip', 0)
        total = preview.get('total', 0)
        ver = preview.get('version', '?')
        bal = preview.get('backup_balance', 0)
        from utils.currency import format_currency as fmt_cur
        bal_str = fmt_cur(bal) if bal else '?'

        preview_text = (
            "📋 <b>Backup Preview — Pending Confirmation</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 <b>Version:</b> {ver}   📌 <b>Revision:</b> {rev}\n"
            f"🕒 <b>Exported at:</b> {exported_at}\n"
            f"💰 <b>Backup balance:</b> {bal_str}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Rows to import:</b> {total} total\n"
            f"  ➕ Add: {to_add}   ✏️ Update: {to_update}   ⏩ Skip: {to_skip}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>This action will merge the backup into the live database.</b>\n"
            "Tap <b>Import</b> to confirm, or <b>Cancel</b> to discard."
        )
        await update.message.reply_text(
            preview_text,
            reply_markup=get_json_import_confirm_keyboard(tmp_token),
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Error handling uploaded JSON document: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Failed to process backup file: {html.escape(str(e))}"
        )
        # Clean up temp file on error
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


async def handle_chat_migration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles Telegram group -> supergroup migration service messages."""
    msg = getattr(update, 'effective_message', None) or getattr(update, 'message', None)
    if not msg:
        return
    old_chat_id = msg.chat.id if hasattr(msg, 'chat') and msg.chat else None
    new_chat_id = getattr(msg, 'migrate_to_chat_id', None)
    if not new_chat_id and getattr(msg, 'migrate_from_chat_id', None):
        old_chat_id = getattr(msg, 'migrate_from_chat_id', None)
        new_chat_id = msg.chat.id if hasattr(msg, 'chat') and msg.chat else None

    if old_chat_id and new_chat_id:
        from database.queries import migrate_workspace_chat_id
        success = migrate_workspace_chat_id(old_chat_id, new_chat_id)
        if success:
            logger.info(f"Telegram chat migration successfully processed: {old_chat_id} -> {new_chat_id}")
        else:
            logger.warning(f"Telegram chat migration failed for: {old_chat_id} -> {new_chat_id}")

