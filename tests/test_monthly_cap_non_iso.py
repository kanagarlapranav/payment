import pytest
from database.models import Transaction
from database.queries import create_custom_workspace, set_workspace_setting, insert_transaction_with_balance
from services.transaction_service import commit_transaction, LimitExceededError

def test_monthly_cap_enforced_on_non_iso_date():
    """
    Fix B verification:
    Verify that transactions with non-ISO dates (like '11-10-2026' or '15/10/2026')
    are correctly bucketed to their intended month, tripping an exceeded monthly cap
    instead of bypassing it via naive string split.
    """
    ws = create_custom_workspace(
        title="Cap Test Ledger",
        creator_user_id=12345,
        username="tester",
        display_name="Tester"
    )
    ws_id = ws.id

    # Set monthly cap of 5000 in this workspace
    set_workspace_setting(ws_id, "monthly_spending_cap", "5000.0")

    # Seed 4000 spent in October 2026
    t1 = Transaction(
        amount=4000.0,
        transaction_type="SENT",
        person_name="Vendor A",
        transaction_date="2026-10-01",
        workspace_id=ws_id,
        telegram_user_id=12345
    )
    insert_transaction_with_balance(t1)

    # Now attempt a new transaction with amount 1500 dated '11-10-2026' (DD-MM-YYYY)
    # Total would be 5500 > 5000 -> must raise LimitExceededError
    t2 = Transaction(
        amount=1500.0,
        transaction_type="SENT",
        person_name="Vendor B",
        transaction_date="11-10-2026",
        workspace_id=ws_id,
        telegram_user_id=12345
    )

    with pytest.raises(LimitExceededError, match="Monthly spending exceeds cap"):
        commit_transaction(t2)

    # Also test slash format '15/10/2026'
    t3 = Transaction(
        amount=1500.0,
        transaction_type="SENT",
        person_name="Vendor C",
        transaction_date="15/10/2026",
        workspace_id=ws_id,
        telegram_user_id=12345
    )

    with pytest.raises(LimitExceededError, match="Monthly spending exceeds cap"):
        commit_transaction(t3)
