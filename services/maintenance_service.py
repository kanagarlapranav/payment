"""
Maintenance Service for Payment Tracker.
Handles scheduled maintenance tasks such as tombstone purges and health routines.
"""
from datetime import datetime, timedelta, timezone

from config import logger
from database.db import LEDGER_LOCK, get_db_connection


def purge_eligible_tombstones(db_path=None) -> int:
    """
    Scheduled maintenance function:
    Purges tombstones older than 365 days ONLY when a backup newer than
    the deletion is confirmed uploaded (tracked via settings 'last_confirmed_backup_at').
    Returns the count of purged tombstones.
    """
    with LEDGER_LOCK, get_db_connection(db_path=db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'last_confirmed_backup_at'")
        row = cursor.fetchone()
        if not row or not row['value']:
            logger.info("Tombstone purge skipped: no confirmed backup upload on record.")
            return 0

        last_backup_iso = row['value']
        now_utc = datetime.now(timezone.utc)
        cutoff_365 = (now_utc - timedelta(days=365)).isoformat()

        # The deletion must be older than 365 days AND older than the confirmed backup
        cursor.execute(
            """
            DELETE FROM transactions
            WHERE deleted_at IS NOT NULL
              AND deleted_at < ?
              AND deleted_at < ?
            """,
            (cutoff_365, last_backup_iso),
        )
        purged = cursor.rowcount
        conn.commit()
        if purged > 0:
            logger.info(
                f"Purged {purged} eligible tombstone(s) older than 365 days backed up before {last_backup_iso}."
            )
        return purged
