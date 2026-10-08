import sys, asyncio; sys.path.insert(0, '.')
import config
from database.db import get_db_connection, LEDGER_LOCK
from utils.dates import utc_now_iso

# Mark dirty and bump revision
with LEDGER_LOCK:
    with get_db_connection() as conn:
        now = utc_now_iso()
        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('is_dirty', '1', ?)", (now,))
        conn.execute("UPDATE settings SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key = 'backup_revision'")
        conn.commit()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = 'backup_revision'")
        print('backup_revision now:', c.fetchone()['value'])

# Export JSON
from services.backup_service import export_database_to_json
export_database_to_json()
print("JSON exported locally.")

# Push to Telegram
async def do_backup():
    from telegram import Bot
    from telegram.request import HTTPXRequest
    req = HTTPXRequest(read_timeout=60.0, write_timeout=60.0)
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN, request=req)
    from services.backup_service import backup_to_telegram
    result = await backup_to_telegram(bot, timeout=30.0)
    print("Backup to Telegram result:", result)
    await bot.close()

asyncio.run(do_backup())
print("Done!")
