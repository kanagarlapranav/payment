"""
Budget tracking and proactive alert service.
Manages monthly budget targets, computes visual progress bars,
and triggers threshold warnings at 50%, 80%, and 100%.
"""
from datetime import datetime
from database.queries import get_budget_setting, set_budget_setting, get_monthly_spending
from database.db import LEDGER_LOCK
from utils.validation import parse_decimal_amount

def set_budget(amount: float) -> str:
    """Sets the monthly spending budget target using Decimal validation and locking."""
    with LEDGER_LOCK:
        try:
            dec_amount = parse_decimal_amount(amount, allow_zero=True)
        except ValueError as err:
            return f"❌ Invalid budget: {err}"
        
        float_amt = float(dec_amount)
        set_budget_setting(float_amt)
        if float_amt == 0.0:
            return "✅ Monthly budget has been disabled (set to ₹0.00)."
        return f"✅ Monthly spending budget set to <b>₹{float_amt:,.2f}</b>."

def get_budget_info(year: int = None, month: int = None) -> dict:
    """Calculates current budget status metrics."""
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    
    budget = get_budget_setting()
    spent = get_monthly_spending(year, month)
    remaining = budget - spent
    percentage = (spent / budget * 100) if budget > 0 else 0.0
    
    # Progress bar (10 blocks)
    filled = min(10, int(round(percentage / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    
    # Status indicator
    if budget <= 0:
        status = "NOT_SET"
        status_text = "No budget configured"
        status_emoji = "⚪"
    elif percentage >= 100:
        status = "EXCEEDED"
        status_text = "Budget Exceeded!"
        status_emoji = "🚨"
    elif percentage >= 80:
        status = "WARNING"
        status_text = "Nearing Budget Limit"
        status_emoji = "⚠️"
    elif percentage >= 50:
        status = "MODERATE"
        status_text = "Halfway Through Budget"
        status_emoji = "🟡"
    else:
        status = "HEALTHY"
        status_text = "Spending On Track"
        status_emoji = "🟢"
        
    return {
        "year": year,
        "month": month,
        "budget": budget,
        "spent": spent,
        "remaining": remaining,
        "percentage": percentage,
        "progress_bar": bar,
        "status": status,
        "status_text": status_text,
        "status_emoji": status_emoji
    }

def format_budget_status(year: int = None, month: int = None) -> str:
    """Formats the monthly budget status into a sleek HTML card for Telegram."""
    info = get_budget_info(year, month)
    
    month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    m_name = month_names[info["month"]] if 1 <= info["month"] <= 12 else str(info["month"])
    
    if info["budget"] <= 0:
        return (
            f"🎯 <b>MONTHLY BUDGET TRACKER</b>\n"
            f"📅 <b>Period:</b> {m_name} {info['year']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ <i>No monthly budget target is currently set.</i>\n\n"
            f"To set a monthly limit, use:\n"
            f"<code>/setbudget 15000</code> (or your chosen amount)"
        )
        
    card = f"🎯 <b>MONTHLY BUDGET TRACKER</b>\n"
    card += f"📅 <b>Period:</b> {m_name} {info['year']}\n"
    card += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    card += f"🎯 <b>Budget Limit:</b> ₹{info['budget']:,.2f}\n"
    card += f"💸 <b>Total Spent:</b> ₹{info['spent']:,.2f}\n"
    
    if info["remaining"] >= 0:
        card += f"💰 <b>Remaining:</b> ₹{info['remaining']:,.2f}\n"
    else:
        card += f"🚨 <b>Over Budget:</b> ₹{abs(info['remaining']):,.2f}\n"
        
    card += f"\n<b>Progress:</b> <code>[{info['progress_bar']}]</code> <b>{info['percentage']:.1f}%</b>\n"
    card += f"{info['status_emoji']} <b>Status:</b> {info['status_text']}\n"
    
    card += f"\n━━━━━━━━━━━━━━━━━━━━\n"
    card += f"<i>Tip: Update anytime using /setbudget &lt;amount&gt;</i>"
    return card

def check_budget_alert(new_tx_amount: float, tx_type: str = "SENT") -> str:
    """
    Checks if an outgoing transaction caused the spending to cross key thresholds (50%, 80%, 100%).
    Returns an alert snippet if a milestone was crossed, or empty string if not.
    """
    if tx_type != "SENT" or new_tx_amount <= 0:
        return ""
        
    now = datetime.now()
    info = get_budget_info(now.year, now.month)
    budget = info["budget"]
    if budget <= 0:
        return ""
        
    spent = info["spent"]
    prev_spent = spent - new_tx_amount
    
    prev_pct = (prev_spent / budget * 100)
    curr_pct = (spent / budget * 100)
    
    alert = ""
    if prev_pct < 100 <= curr_pct:
        alert = (
            f"\n\n🚨 <b>BUDGET ALERT: 100% EXCEEDED!</b>\n"
            f"You have spent ₹{spent:,.2f} of your ₹{budget:,.2f} budget "
            f"<code>[{info['progress_bar']}]</code> ({curr_pct:.1f}%)."
        )
    elif prev_pct < 80 <= curr_pct:
        alert = (
            f"\n\n⚠️ <b>BUDGET ALERT: 80% LIMIT REACHED!</b>\n"
            f"You have used <b>{curr_pct:.1f}%</b> of your monthly budget (₹{spent:,.2f} / ₹{budget:,.2f})."
        )
    elif prev_pct < 50 <= curr_pct:
        alert = (
            f"\n\n🟡 <b>BUDGET NOTICE: 50% Used</b>\n"
            f"You've crossed halfway through your monthly budget (₹{spent:,.2f} / ₹{budget:,.2f})."
        )
        
    return alert
