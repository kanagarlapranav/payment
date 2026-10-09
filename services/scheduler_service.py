"""
Scheduler and Daily Financial Digest Service using python-telegram-bot JobQueue.
Handles:
1. Daily Financial Digest run_daily via ZoneInfo("Asia/Kolkata") at DAILY_DIGEST_TIME with retry and idempotent sent tracking.
2. 60-second Cloud Backup Retry Job for dirty databases.
3. 24-hour Tombstone Purge Maintenance Job.
"""

import asyncio
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes

import config
from config import (
    logger, TELEGRAM_USER_ID, TELEGRAM_GROUP_ID,
    DEFAULT_TIMEZONE
)
from database.db import get_db_connection, LEDGER_LOCK
from database.queries import (
    get_daily_summary_stats, get_balance_setting, get_monthly_spending, get_budget_setting
)
from services.category_service import get_category_icon
from utils.html_safety import escape_html, sanitize_gemini_html, send_safe_message
from utils.dates import utc_now_iso

# ZoneInfo for Indian Standard Time
IST_TZ = ZoneInfo(getattr(config, "DEFAULT_TIMEZONE", "Asia/Kolkata") or "Asia/Kolkata")


def get_current_ist_time() -> datetime:
    """Returns current datetime in Indian Standard Time (IST)."""
    return datetime.now(IST_TZ)


def parse_digest_time() -> time:
    """Parses DAILY_DIGEST_TIME string (e.g. '22:00') into a timezone-aware datetime.time."""
    try:
        raw = str(getattr(config, "DAILY_DIGEST_TIME", "22:00") or "22:00").strip()
        parts = raw.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return time(hour=hour, minute=minute, tzinfo=IST_TZ)
    except Exception as e:
        logger.warning(f"Invalid DAILY_DIGEST_TIME ({e}), defaulting to 22:00 IST.")
        return time(hour=22, minute=0, tzinfo=IST_TZ)


def format_daily_digest(target_date_str: str = None, workspace_id: str = None) -> str:
    """
    Formats the daily closing financial briefing into a structured HTML report
    with strict HTML safety escaping on all dynamic variables.
    """
    if not target_date_str:
        target_date_str = get_current_ist_time().strftime("%Y-%m-%d")

    stats = get_daily_summary_stats(target_date_str, workspace_id=workspace_id)
    current_balance = get_balance_setting(workspace_id=workspace_id)

    try:
        d_obj = datetime.strptime(target_date_str, "%Y-%m-%d")
        display_date = d_obj.strftime("%A, %d %B %Y")
    except Exception:
        display_date = target_date_str

    header = f"🌙 <b>DAILY FINANCIAL DIGEST</b>\n"
    header += f"📅 <b>Date:</b> {escape_html(display_date)}\n"
    header += "━━━━━━━━━━━━━━━━━━━━\n\n"

    total_sent = stats["total_sent"]
    total_received = stats["total_received"]
    net_change = stats["net_change"]
    tx_count = stats["tx_count"]
    txs = stats["transactions"]

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
            t_type = t.get("transaction_type", "")
            amt = float(t.get("amount", 0.0))
            name = escape_html(t.get("person_name") or "Unknown")
            cat = escape_html(t.get("category") or "General")
            icon = get_category_icon(t.get("category") or "General")
            t_time = escape_html(t.get("transaction_time") or "")
            time_tag = f" ({t_time})" if t_time else ""

            if t_type == "RECEIVED":
                summary += f"• 🟢 <b>+₹{amt:,.2f}</b> from <b>{name}</b> ({cat}) {icon}{time_tag}\n"
            elif t_type == "TRANSFER":
                summary += f"• 🔄 <b>₹{amt:,.2f}</b> Transfer: <b>{name}</b> ({cat}) {icon}{time_tag}\n"
            else:
                summary += f"• 🔴 <b>-₹{amt:,.2f}</b> to <b>{name}</b> ({cat}) {icon}{time_tag}\n"

    now_ist = get_current_ist_time()
    budget = get_budget_setting(workspace_id=workspace_id)
    if budget > 0:
        monthly_spent = get_monthly_spending(now_ist.year, now_ist.month, workspace_id=workspace_id)
        pct = (monthly_spent / budget * 100)
        summary += f"\n🎯 <b>Monthly Budget Pace:</b> ₹{monthly_spent:,.2f} / ₹{budget:,.2f} ({pct:.1f}%)\n"

    # Gemini AI Closing Remarks with allowlisted HTML
    try:
        from ocr.gemini_vision import generate_gemini_daily_commentary, is_gemini_available
        if is_gemini_available():
            day_data = f"Date: {target_date_str}, Spent: Rs.{total_sent}, Received: Rs.{total_received}, Net: Rs.{net_change}, Count: {tx_count}"
            ai_remark = generate_gemini_daily_commentary(day_data)
            if ai_remark:
                clean_remark = sanitize_gemini_html(ai_remark)
                summary += f"\n🤖 <b>Gemini AI Takeaway:</b>\n{clean_remark}\n"
    except Exception:
        pass

    raw_time_str = str(getattr(config, "DAILY_DIGEST_TIME", "22:00") or "22:00")
    summary += "\n━━━━━━━━━━━━━━━━━━━━\n"
    summary += f"<i>Automated {escape_html(raw_time_str)} IST Closing Briefing. Have a great night! 🌙</i>"

    return header + summary


async def send_daily_digest_with_retry(bot, target_chat_id: int | str, target_date_str: str, workspace_id: str = None) -> bool:
    """
    Sends the daily digest to target_chat_id with up to 3 retry attempts with exponential backoff.
    Marks the date as sent in database settings / workspace_settings ONLY after successful delivery.
    """
    digest_text = await asyncio.to_thread(format_daily_digest, target_date_str, workspace_id=workspace_id)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            logger.info(f"Sending daily digest for {target_date_str} to chat {target_chat_id} (workspace={workspace_id}) (attempt {attempt + 1}/{max_retries})...")
            await send_safe_message(bot, target_chat_id, digest_text, parse_mode="HTML")
            
            # Mark as successfully sent in settings and workspace_settings
            with LEDGER_LOCK:
                with get_db_connection() as conn:
                    now_utc = utc_now_iso()
                    if workspace_id:
                        conn.execute(
                            "INSERT OR REPLACE INTO workspace_settings (workspace_id, key, value, updated_at) VALUES (?, 'last_digest_sent_date', ?, ?)",
                            (workspace_id, target_date_str, now_utc)
                        )
                    from database.queries import get_default_workspace_id
                    if not workspace_id or workspace_id == get_default_workspace_id():
                        conn.execute(
                            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('last_digest_sent_date', ?, ?)",
                            (target_date_str, now_utc)
                        )
                    conn.commit()

            logger.info(f"Successfully delivered daily digest for {target_date_str} to chat {target_chat_id} (workspace={workspace_id}).")
            return True
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} to send daily digest for ws={workspace_id} to chat={target_chat_id} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2.0 * (attempt + 1))

    logger.error(f"Failed to send daily digest for {target_date_str} to chat {target_chat_id} after {max_retries} attempts.")
    return False


def claim_workspace_job(workspace_id: str, job_name: str, scheduled_date: str, lease_timeout_seconds: int = 900) -> bool:
    """
    Attempts to claim execution of a scheduled job for a workspace on a specific date.
    Returns True if claimed, False if already completed or active running lease exists.
    """
    now_utc = utc_now_iso()
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO workspace_job_runs (
                        workspace_id, job_name, scheduled_date, status, executed_at
                    ) VALUES (?, ?, ?, 'running', ?)
                """, (str(workspace_id), str(job_name), str(scheduled_date), now_utc))
                conn.commit()
                if cursor.rowcount == 1:
                    return True

                cursor.execute("""
                    SELECT status, executed_at FROM workspace_job_runs
                    WHERE workspace_id = ? AND job_name = ? AND scheduled_date = ?
                """, (str(workspace_id), str(job_name), str(scheduled_date)))
                row = cursor.fetchone()
                if not row or row['status'] == 'completed':
                    return False

                if row['status'] == 'running':
                    exec_at = row['executed_at']
                    is_expired = False
                    try:
                        from utils.dates import parse_utc_iso
                        dt = parse_utc_iso(exec_at)
                        if dt and (datetime.now(timezone.utc) - dt).total_seconds() > lease_timeout_seconds:
                            is_expired = True
                    except Exception:
                        is_expired = True

                    if is_expired:
                        cursor.execute("""
                            UPDATE workspace_job_runs
                            SET status = 'running', executed_at = ?
                            WHERE workspace_id = ? AND job_name = ? AND scheduled_date = ?
                              AND status = 'running' AND executed_at = ?
                        """, (now_utc, str(workspace_id), str(job_name), str(scheduled_date), exec_at))
                        conn.commit()
                        return cursor.rowcount == 1

                return False
    except Exception as e:
        logger.warning(f"Failed to claim job {job_name} for workspace {workspace_id}: {e}")
        return False


def complete_workspace_job(workspace_id: str, job_name: str, scheduled_date: str, status: str = "completed"):
    """Marks a workspace job run with its final execution status ('completed' or 'failed')."""
    now_utc = utc_now_iso()
    try:
        with LEDGER_LOCK:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE workspace_job_runs
                    SET status = ?, executed_at = ?
                    WHERE workspace_id = ? AND job_name = ? AND scheduled_date = ?
                """, (status, now_utc, str(workspace_id), str(job_name), str(scheduled_date)))
                conn.commit()
    except Exception as e:
        logger.warning(f"Failed to record job status for workspace {workspace_id}: {e}")


async def daily_digest_job(context: ContextTypes.DEFAULT_TYPE):
    """PTB JobQueue handler for daily financial digest with multi-workspace fan-out and per-workspace failure isolation."""
    today_str = get_current_ist_time().strftime("%Y-%m-%d")

    # 1. Try fan-out to all active workspaces
    from database.queries import get_active_workspaces, get_workspace_setting
    active_workspaces = []
    try:
        active_workspaces = get_active_workspaces()
    except Exception as e:
        logger.warning(f"Failed to query active workspaces for digest: {e}")

    if active_workspaces:
        for ws in active_workspaces:
            try:
                ws_id = ws['id']
                chat_id = ws['chat_id']
                
                # Check per-workspace enabled flag
                if get_workspace_setting(ws_id, 'daily_digest_enabled') == '0':
                    continue

                last_sent = get_workspace_setting(ws_id, 'last_digest_sent_date')
                if last_sent == today_str:
                    logger.info(f"Daily digest for workspace {ws_id} already sent today. Skipping.")
                    continue

                # Idempotent job claim check
                if not claim_workspace_job(ws_id, "daily_digest", today_str):
                    logger.info(f"Daily digest for workspace {ws_id} already claimed/completed. Skipping.")
                    continue

                success = await send_daily_digest_with_retry(context.bot, chat_id, today_str, workspace_id=ws_id)
                complete_workspace_job(ws_id, "daily_digest", today_str, "completed" if success else "failed")

                # Jitter rate limiting (100ms) to respect Telegram API 30 msg/s ceiling
                await asyncio.sleep(0.1)
            except Exception as ws_err:
                logger.error(f"Error executing daily digest for workspace {ws.get('id', '?')}: {ws_err}")
                continue
        return

    # 2. Legacy fallback if no workspaces are found
    target_chat_id = TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
    if not target_chat_id:
        logger.warning("Daily digest job skipped: no TELEGRAM_GROUP_ID or TELEGRAM_USER_ID configured.")
        return

    # Check if already sent today
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'last_digest_sent_date'")
        row = cur.fetchone()
        last_sent = row["value"] if row else ""

    if last_sent == today_str:
        logger.info(f"Daily digest for {today_str} already sent. Skipping.")
        return

    await send_daily_digest_with_retry(context.bot, target_chat_id, today_str)


async def backup_retry_job(context: ContextTypes.DEFAULT_TYPE):
    """PTB JobQueue handler for periodic dirty backup retries (runs every 60s)."""
    try:
        is_dirty = False
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = 'is_dirty'")
            row = cur.fetchone()
            if row and row["value"] == "1":
                is_dirty = True

        if is_dirty:
            logger.info("Backup retry job: database is dirty. Triggering cloud backup to Telegram...")
            from services.backup_service import backup_to_telegram
            target_chat_id = TELEGRAM_GROUP_ID or TELEGRAM_USER_ID
            await backup_to_telegram(context.bot, target_chat_id)
    except Exception as e:
        logger.debug(f"Backup retry job notice: {e}")


async def tombstone_purge_job(context: ContextTypes.DEFAULT_TYPE):
    """PTB JobQueue handler for purging expired tombstones (runs every 24h)."""
    try:
        from services.maintenance_service import purge_eligible_tombstones
        purged = await asyncio.to_thread(purge_eligible_tombstones)
        if purged:
            logger.info(f"Tombstone purge job completed: purged {purged} old records.")
    except Exception as e:
        logger.debug(f"Tombstone purge job notice: {e}")


def register_scheduler_jobs(application):
    """Registers all recurring and daily scheduled jobs with python-telegram-bot's JobQueue."""
    if not hasattr(application, "job_queue") or application.job_queue is None:
        logger.warning("Application has no JobQueue initialized. Scheduled jobs cannot be registered.")
        return

    digest_time = parse_digest_time()
    logger.info(f"Registering daily digest job at {digest_time.strftime('%H:%M')} IST...")
    application.job_queue.run_daily(
        daily_digest_job,
        time=digest_time,
        name="daily_digest_job"
    )

    logger.info("Registering periodic dirty backup retry job (every 60s)...")
    application.job_queue.run_repeating(
        backup_retry_job,
        interval=60,
        first=30,
        name="backup_retry_job"
    )

    logger.info("Registering daily tombstone purge maintenance job (every 24h)...")
    application.job_queue.run_repeating(
        tombstone_purge_job,
        interval=86400,
        first=60,
        name="tombstone_purge_job"
    )
