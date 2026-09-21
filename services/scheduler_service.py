"""
Scheduler and Daily Financial Digest Service.
Generates comprehensive daily closing summaries and runs a lightweight background
timer to send automatic daily digests at 9:00 PM IST.
"""
import time
import threading
from datetime import datetime, timezone, timedelta
from database.queries import get_daily_summary_stats, get_balance_setting, get_monthly_spending, get_budget_setting
from services.category_service import get_category_icon
from config import logger, TELEGRAM_USER_ID, TELEGRAM_GROUP_ID

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

def get_current_ist_time() -> datetime:
    """Returns the current datetime in Indian Standard Time (IST)."""
    return datetime.now(IST)

def format_daily_digest(target_date_str: str = None) -> str:
    """
    Formats the daily closing financial briefing into a structured HTML report.
    If target_date_str is None, uses today's date in IST (YYYY-MM-DD).
    """
    if not target_date_str:
        target_date_str = get_current_ist_time().strftime("%Y-%m-%d")
        
    stats = get_daily_summary_stats(target_date_str)
    current_balance = get_balance_setting()
    
    # Parse date for presentation
    try:
        d_obj = datetime.strptime(target_date_str, "%Y-%m-%d")
        display_date = d_obj.strftime("%A, %d %B %Y")
    except Exception:
        display_date = target_date_str
        
    header = f"🌙 <b>DAILY FINANCIAL DIGEST</b>\n"
    header += f"📅 <b>Date:</b> {display_date}\n"
    header += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    total_sent = stats['total_sent']
    total_received = stats['total_received']
    net_change = stats['net_change']
    tx_count = stats['tx_count']
    txs = stats['transactions']
    
    summary = f"📈 <b>Money Received:</b> ₹{total_received:,.2f}\n"
    summary += f"📉 <b>Money Spent:</b> ₹{total_sent:,.2f}\n"
    
    if net_change > 0:
        summary += f"🟢 <b>Day's Net Gain:</b> +₹{net_change:,.2f}\n"
    elif net_change < 0:
        summary += f"🔴 <b>Day's Net Spend:</b> -₹{abs(net_change):,.2f}\n"
    else:
        summary += f"⚪ <b>Day's Net:</b> ₹0.00\n"
        
    summary += f"💼 <b>Closing Balance:</b> <b>₹{current_balance:,.2f}</b>\n"
    summary += f"🔢 <b>Transactions Today:</b> {tx_count}\n\n"
    
    if not txs:
        summary += "<i>No transactions recorded on this day.</i>\n"
    else:
        summary += "📝 <b>Today's Activity Log:</b>\n"
        for t in txs:
            t_type = t.get('transaction_type', '')
            amt = float(t.get('amount', 0.0))
            name = t.get('person_name', 'Unknown')
            cat = t.get('category', 'General')
            icon = get_category_icon(cat)
            t_time = t.get('transaction_time') or ''
            time_tag = f" ({t_time})" if t_time else ""
            
            if t_type == "RECEIVED":
                summary += f"• 🟢 <b>+₹{amt:,.2f}</b> from <b>{name}</b> {icon}{time_tag}\n"
            else:
                summary += f"• 🔴 <b>-₹{amt:,.2f}</b> to <b>{name}</b> {icon}{time_tag}\n"
                
    # Monthly pace note
    now_ist = get_current_ist_time()
    budget = get_budget_setting()
    if budget > 0:
        monthly_spent = get_monthly_spending(now_ist.year, now_ist.month)
        pct = (monthly_spent / budget * 100)
        summary += f"\n🎯 <b>Monthly Budget Pace:</b> ₹{monthly_spent:,.2f} / ₹{budget:,.2f} ({pct:.1f}%)\n"

    # Gemini AI Closing Remarks
    try:
        from ocr.gemini_vision import generate_gemini_daily_commentary, is_gemini_available
        if is_gemini_available():
            day_data = f"Date: {date_str}, Spent: Rs.{total_sent}, Received: Rs.{total_received}, Net: Rs.{net_change}, Count: {tx_count}"
            ai_remark = generate_gemini_daily_commentary(day_data)
            if ai_remark:
                summary += f"\n🤖 <b>Gemini AI Takeaway:</b>\n{ai_remark}\n"
    except Exception:
        pass
        
    summary += "\n━━━━━━━━━━━━━━━━━━━━\n"
    summary += "<i>Automated 9:00 PM IST Closing Briefing. Have a great night! 🌙</i>"
    
    return header + summary


class DailyDigestScheduler:
    """Lightweight background thread that checks IST time and delivers 9:00 PM digests."""
    def __init__(self, bot_instance=None, target_chat_id=None):
        self.bot = bot_instance
        self.target_chat_id = target_chat_id or TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
        self.last_sent_date = None
        self._running = False
        self._thread = None
        
    def set_bot(self, bot_instance, chat_id=None):
        self.bot = bot_instance
        if chat_id:
            self.target_chat_id = chat_id
            
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DailyDigestThread")
        self._thread.start()
        logger.info("Daily 9:00 PM Digest Scheduler started.")
        
    def stop(self):
        self._running = False
        
    def _run_loop(self):
        while self._running:
            try:
                now = get_current_ist_time()
                today_str = now.strftime("%Y-%m-%d")
                
                # Check if it is 21:00 (9:00 PM IST) and hasn't been sent for today
                if now.hour == 21 and now.minute >= 0 and self.last_sent_date != today_str:
                    if self.bot and self.target_chat_id:
                        try:
                            digest_text = format_daily_digest(today_str)
                            self.bot.send_message(
                                chat_id=self.target_chat_id,
                                text=digest_text,
                                parse_mode='HTML'
                            )
                            self.last_sent_date = today_str
                            logger.info(f"Sent 9:00 PM daily digest for {today_str} to chat {self.target_chat_id}")
                        except Exception as send_err:
                            logger.error(f"Failed to send scheduled daily digest: {send_err}")

                        # Run daily maintenance: eligible tombstone purge
                        try:
                            from services.maintenance_service import purge_eligible_tombstones
                            purge_eligible_tombstones()
                        except Exception as m_err:
                            logger.debug(f"Scheduled maintenance notice: {m_err}")
                            
                # Sleep for 30 seconds before next check
                time.sleep(30)
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                time.sleep(60)

# Global scheduler singleton
scheduler = DailyDigestScheduler()
