from parsers import get_best_parser
from database.models import Transaction
from database.queries import insert_transaction
from services.balance_service import update_balance_for_transaction
from services.duplicate_service import is_duplicate
from config import logger

_PROMO_PATTERNS = (
    'cashback', 'download now', 'onelink', 'playstore', 'appstore',
    'get up to', 'win up to', 'scratch card', 'refer and earn',
    'invite and earn', 'install now', 'bit.ly/', 'goo.gl/',
)

def _strip_promo_lines(text: str) -> str:
    """Removes promotional / ad lines so their amounts don't pollute parsing."""
    cleaned = []
    for line in text.split('\n'):
        ll = line.lower()
        if any(kw in ll for kw in _PROMO_PATTERNS):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def process_transaction(raw_text: str, image_path: str, message_id: str, chat_id: str, caption: str = "") -> tuple[Transaction, int]:
    """
    Core pipeline: Parses text, evaluates confidence, checks duplicates, updates balance, and saves.
    Returns: (Transaction object, confidence score)
    """
    full_text = f"{raw_text}\n{caption}".strip() if caption else raw_text
    # Strip promotional/ad lines BEFORE parsing so promo amounts (e.g. "₹300 cashback") are never seen
    full_text = _strip_promo_lines(full_text)
    logger.info("Selecting parser...")
    parser = get_best_parser(full_text)
    logger.info(f"Selected parser: {parser.__class__.__name__}")
    
    transaction = parser.parse()
    transaction.original_image_path = image_path
    transaction.telegram_message_id = message_id
    transaction.telegram_chat_id = chat_id
    
    confidence = parser.get_confidence(transaction)
    logger.info(f"Parsed transaction with confidence: {confidence}")
    
    return transaction, confidence

def commit_transaction(transaction: Transaction) -> bool:
    """
    Saves the transaction to DB and updates balance.
    Must be called only if confident or after user confirmation.
    """
    try:
        # Check duplicate
        if is_duplicate(transaction):
            logger.warning("Duplicate transaction detected during commit.")
            return False
            
        # Update balance
        transaction = update_balance_for_transaction(transaction)
        
        # Save to DB
        transaction_id = insert_transaction(transaction)
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
