"""
Auto-categorization and financial spending insights service.
Classifies transactions into smart categories using rules + AI heuristics,
and generates rich monthly spending analytics.
"""
import re
from database.queries import get_category_summary, get_monthly_summary, get_monthly_spending, get_budget_setting
from config import logger

CATEGORIES = {
    "Food & Dining": ["swiggy", "zomato", "restaurant", "cafe", "mcdonald", "kfc", "burger", "pizza", "bakery", "food", "dhaba", "dining", "tea", "chai", "coffee", "bistro", "eatery", "canteen"],
    "Groceries": ["blinkit", "zepto", "instamart", "bigbasket", "supermarket", "mart", "provision", "vegetable", "fruits", "dmart", "spencer", "reliance fresh", "milk", "kirana"],
    "Shopping": ["amazon", "flipkart", "myntra", "ajio", "meesho", "store", "retail", "clothing", "apparel", "shoes", "mall", "electronics", "croma", "reliancedigital"],
    "Travel & Transport": ["uber", "ola", "rapido", "irctc", "petrol", "fuel", "diesel", "toll", "metro", "flight", "indigo", "air india", "redbus", "abhibus", "parking", "fastag", "cab", "auto"],
    "Bills & Utilities": ["electricity", "water", "broadband", "wifi", "recharge", "jio", "airtel", "vi", "vodafone", "gas", "cylinder", "bill", "dth", "tatasky", "rent", "maintenance", "icic admin", "admin"],
    "Entertainment": ["bookmyshow", "netflix", "prime video", "hotstar", "spotify", "cinema", "theatre", "movie", "pvr", "inox", "youtube", "game", "steam"],
    "Health & Medical": ["pharmacy", "apollo", "medplus", "hospital", "clinic", "doctor", "lab", "diagnostics", "medicine", "1mg", "pharmeasy", "dental", "care"],
    "Transfers & P2P": ["transfer", "p2p", "upi", "received", "friend", "family", "vamsi", "vinay", "siddhu", "motru", "pranav"]
}

CATEGORY_ICONS = {
    "Food & Dining": "🍔",
    "Groceries": "🛒",
    "Shopping": "🛍️",
    "Travel & Transport": "🚕",
    "Bills & Utilities": "⚡",
    "Entertainment": "🎬",
    "Health & Medical": "💊",
    "Transfers & P2P": "👥",
    "General": "💳"
}

def predict_category(text: str = "", person_name: str = "", tx_type: str = "") -> str:
    """
    Predicts transaction category based on OCR/description text, person/merchant name, and transaction type.
    """
    combined = f"{text} {person_name}".lower()
    
    # Check rule-based keywords
    for cat, keywords in CATEGORIES.items():
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}\b", combined, re.IGNORECASE):
                return cat
                
    # If it's a person transfer with no merchant keywords, default to Transfers & P2P
    if person_name and person_name.strip():
        # If it looks like a person's name (2-3 words, no corporate terms)
        words = person_name.strip().split()
        if 1 <= len(words) <= 4 and not any(w in combined for w in ["pvt", "ltd", "corp", "inc", "store", "shop", "service"]):
            return "Transfers & P2P"
            
    if tx_type == "RECEIVED":
        return "Transfers & P2P"
        
    return "General"

def get_category_icon(category: str) -> str:
    """Returns the matching emoji icon for a category."""
    return CATEGORY_ICONS.get(category, "💳")

def format_spending_insights(year: int, month: int) -> str:
    """
    Generates a rich HTML report of monthly spending insights, category breakdown,
    and financial health highlights for Telegram.
    """
    cat_data = get_category_summary(year, month)
    monthly_data = get_monthly_summary(year, month)
    budget = get_budget_setting()
    
    month_names = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    m_name = month_names[month] if 1 <= month <= 12 else str(month)
    
    total_spent = monthly_data['total_sent']
    total_received = monthly_data['total_received']
    net_savings = monthly_data['net_savings']
    tx_count = monthly_data['tx_count']
    
    # Filter only SENT for spending breakdown
    sent_by_cat = [c for c in cat_data if c['transaction_type'] == 'SENT']
    
    header = f"📊 <b>SPENDING INSIGHTS & ANALYTICS</b>\n"
    header += f"📅 <b>Period:</b> {m_name} {year}\n"
    header += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    metrics = f"💰 <b>Total Income:</b> ₹{total_received:,.2f}\n"
    metrics += f"💸 <b>Total Spent:</b> ₹{total_spent:,.2f}\n"
    
    if net_savings >= 0:
        metrics += f"🟢 <b>Net Savings:</b> +₹{net_savings:,.2f}\n"
    else:
        metrics += f"🔴 <b>Net Deficit:</b> -₹{abs(net_savings):,.2f}\n"
        
    metrics += f"📝 <b>Total Transactions:</b> {tx_count}\n"
    
    # Budget evaluation if set
    if budget > 0:
        pct = (total_spent / budget) * 100
        filled = min(10, int(round(pct / 10)))
        bar = "█" * filled + "░" * (10 - filled)
        metrics += f"\n🎯 <b>Budget Status:</b> ₹{total_spent:,.2f} / ₹{budget:,.2f}\n"
        metrics += f"<code>[{bar}]</code> {pct:.1f}%\n"
        if pct > 100:
            metrics += f"⚠️ <b>Over Budget by:</b> ₹{total_spent - budget:,.2f}\n"
            
    metrics += "\n🏷️ <b>Category Breakdown (Spending):</b>\n"
    
    if not sent_by_cat:
        metrics += "<i>No outgoing expenses recorded for this month.</i>\n"
    else:
        for item in sent_by_cat:
            cat_name = item['category'] or 'General'
            amt = float(item['total_amount'])
            icon = get_category_icon(cat_name)
            share_pct = (amt / total_spent * 100) if total_spent > 0 else 0.0
            filled_mini = min(8, int(round(share_pct / 12.5)))
            mini_bar = "■" * filled_mini + "□" * (8 - filled_mini)
            metrics += f"{icon} <b>{cat_name}</b>: ₹{amt:,.2f} ({share_pct:.1f}%)\n"
            metrics += f"   <code>[{mini_bar}]</code> ({item['count']} tx)\n"
            
    # Key Observations & AI Takeaways
    metrics += "\n💡 <b>Smart Observations:</b>\n"
    if sent_by_cat:
        top_cat = sent_by_cat[0]
        top_name = top_cat['category'] or 'General'
        top_pct = (float(top_cat['total_amount']) / total_spent * 100) if total_spent > 0 else 0
        metrics += f"• Largest expense driver is <b>{top_name}</b> at <b>{top_pct:.1f}%</b> of total spending.\n"
    
    if total_received > 0:
        savings_rate = (net_savings / total_received) * 100
        if savings_rate > 30:
            metrics += f"• Great savings rate of <b>{savings_rate:.1f}%</b> this month! Keep it up. 🌟\n"
        elif savings_rate > 0:
            metrics += f"• Moderate savings rate of <b>{savings_rate:.1f}%</b>. Consider reducing discretionary costs.\n"
        else:
            metrics += f"• Spending exceeds income this month. Watch out for non-essential transfers.\n"
            
    metrics += "\n━━━━━━━━━━━━━━━━━━━━\n"
    metrics += "<i>Tip: Use /dashboard to view interactive graphical charts.</i>"
    
    return header + metrics
