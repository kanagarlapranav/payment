from database.queries import get_transaction_by_reference
from database.models import Transaction

def is_duplicate(transaction: Transaction) -> bool:
    """
    Checks if a transaction is a duplicate.
    Currently uses reference number if available.
    """
    if not transaction.reference_number:
        # If there's no reference number, we could use image hashing or a combination
        # of date, amount, and person. For safety, if no reference, we don't flag as dup automatically
        # unless we implement deep comparison.
        return False
        
    existing = get_transaction_by_reference(transaction.reference_number)
    return existing is not None
