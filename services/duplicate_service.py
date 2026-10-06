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
        
    ws_id = getattr(transaction, 'workspace_id', None)
    existing = get_transaction_by_reference(transaction.reference_number, workspace_id=ws_id)
    return existing is not None
