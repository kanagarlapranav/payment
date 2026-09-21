from parsers import get_best_parser
from database.models import Transaction
from database.queries import insert_transaction
from database.db import LEDGER_LOCK
from services.balance_service import update_balance_for_transaction
from services.duplicate_service import is_duplicate
from utils.validation import (
    parse_decimal_amount,
    validate_transaction_type,
    validate_string_length,
    validate_caption,
)
from config import logger

_PROMO_PATTERNS = (
    'cashback', 'download now', 'onelink', 'playstore', 'appstore',
    'get up to', 'win up to', 'scratch card', 'refer and earn',
    'invite and earn', 'install now', 'bit.ly/', 'goo.gl/',
    'link.super.money', 'sent you payment via upi',
    'let me know when you get it', 'monthlybudgetpace',
    'pinned message', 'auto-backup', '#payment_tracker_backup',
    'paymentsent', 'paymentreceived'
)

def _strip_promo_lines(text: str) -> str:
    """Removes promotional, URL, and previous bot chat history lines so they don't pollute parsing."""
    cleaned = []
    for line in text.split('\n'):
        ll = line.lower().replace(' ', '').replace('_', '')
        if any(kw.replace(' ', '') in ll for kw in _PROMO_PATTERNS):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)



def process_transaction(raw_text: str, image_path: str, message_id: str, chat_id: str, caption: str = "") -> tuple[Transaction, int]:
    """
    Core pipeline: Parses text, evaluates confidence, checks duplicates, updates balance, and saves.
    Uses Gemini AI as primary extractor for both natural text and vision receipts, with regex/OCR backup.
    Returns: (Transaction object, confidence score)
    """
    caption = validate_caption(caption)
    full_text = f"{raw_text}\n{caption}".strip() if caption else raw_text
    full_text = _strip_promo_lines(full_text)

    # If it's a typed text message (no image), try Gemini Natural Language processing first
    if not image_path and full_text:
        try:
            from ocr.gemini_vision import parse_text_with_gemini
            g_tx, g_conf = parse_text_with_gemini(full_text)
            if g_tx and g_tx.amount and g_tx.amount > 0:
                g_tx.telegram_message_id = message_id
                g_tx.telegram_chat_id = chat_id
                from services.category_service import predict_category
                if not getattr(g_tx, 'category', None) or g_tx.category == 'General':
                    g_tx.category = predict_category(
                        text=full_text,
                        person_name=g_tx.person_name or "",
                        tx_type=g_tx.transaction_type or ""
                    )
                logger.info(f"Gemini AI parsed natural text transaction: {g_tx.transaction_type} Rs. {g_tx.amount} to/from {g_tx.person_name}")
                return g_tx, g_conf
        except Exception as g_err:
            logger.debug(f"Gemini text parsing fallback: {g_err}")

    logger.info("Selecting regex heuristic parser...")
    parser = get_best_parser(full_text)
    logger.info(f"Selected parser: {parser.__class__.__name__}")
    
    transaction = parser.parse()
    transaction.original_image_path = image_path
    transaction.telegram_message_id = message_id
    transaction.telegram_chat_id = chat_id
    
    confidence = parser.get_confidence(transaction)
    logger.info(f"Parsed transaction with confidence: {confidence}")
    
    # Auto-predict category
    from services.category_service import predict_category
    if not getattr(transaction, 'category', None) or transaction.category == 'General':
        transaction.category = predict_category(
            text=full_text,
            person_name=transaction.person_name or "",
            tx_type=transaction.transaction_type or ""
        )
    
    return transaction, confidence


def commit_transaction(transaction: Transaction) -> bool:
    """
    Saves the transaction to DB and updates balance under LEDGER_LOCK with Decimal precision.
    Must be called only if confident or after user confirmation.
    """
    with LEDGER_LOCK:
        try:
            # Validate core transaction fields
            dec_amt = parse_decimal_amount(transaction.amount, allow_zero=False)
            transaction.amount = float(dec_amt)
            transaction.transaction_type = validate_transaction_type(transaction.transaction_type)
            
            transaction.person_name = validate_string_length(transaction.person_name, max_length=120, field_name="Person name")
            transaction.sender_name = validate_string_length(transaction.sender_name, max_length=120, field_name="Sender name")
            transaction.recipient_name = validate_string_length(transaction.recipient_name, max_length=120, field_name="Recipient name")
            transaction.reference_number = validate_string_length(transaction.reference_number, max_length=100, field_name="Reference number")

            # Check duplicate
            if is_duplicate(transaction):
                logger.warning("Duplicate transaction detected during commit.")
                return False
                
            # Ensure category is populated
            if not getattr(transaction, 'category', None) or transaction.category == 'General':
                from services.category_service import predict_category
                transaction.category = predict_category(
                    text=transaction.ocr_text or "",
                    person_name=transaction.person_name or "",
                    tx_type=transaction.transaction_type or ""
                )
                
            # Save to DB and atomically recalculate entire balance chain
            from database.queries import insert_transaction_with_balance
            transaction_id = insert_transaction_with_balance(transaction)
            transaction.id = transaction_id
            
            # Keep local JSON snapshot in sync
            try:
                from services.backup_service import export_database_to_json
                export_database_to_json()
            except Exception as bkp_err:
                logger.warning(f"Could not update local JSON backup: {bkp_err}")
            
            logger.info(f"Transaction committed successfully. ID: {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"Error committing transaction: {e}")
            raise e
