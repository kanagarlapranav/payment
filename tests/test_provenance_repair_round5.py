import sqlite3
import pytest
from pathlib import Path
from scripts.repair_workspace_provenance import repair_provenance
import config


SYNTHETIC_OWNER_ID = 8379948573
SYNTHETIC_MEMBER_ID = 555111222
SYNTHETIC_UNKNOWN_USER_ID = 999888777


@pytest.fixture
def fresh_db_setup(tmp_path, monkeypatch):
    """Sets up an isolated SQLite DB simulating a fresh-DB clone where rows carry old/dead workspace IDs."""
    db_file = tmp_path / "test_fresh_db.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("config.TELEGRAM_USER_ID", SYNTHETIC_OWNER_ID)

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            chat_id INTEGER UNIQUE NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'private',
            title TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT,
            telegram_chat_id TEXT,
            telegram_user_id INTEGER,
            amount REAL NOT NULL,
            transaction_type TEXT NOT NULL DEFAULT 'SENT',
            person_name TEXT DEFAULT '',
            sender_name TEXT DEFAULT '',
            recipient_name TEXT DEFAULT '',
            transaction_date TEXT,
            transaction_time TEXT,
            reference_number TEXT,
            category TEXT DEFAULT 'General',
            balance_before REAL DEFAULT 0.0,
            balance_after REAL DEFAULT 0.0,
            occurred_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT DEFAULT NULL,
            uid TEXT UNIQUE
        )
    """)

    cursor.execute("""
        CREATE TABLE undo_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT,
            chat_id INTEGER,
            user_id INTEGER,
            action TEXT NOT NULL,
            uid TEXT NOT NULL,
            snapshot_json TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE workspace_settings (
            workspace_id TEXT,
            key TEXT,
            value TEXT,
            updated_at TEXT,
            PRIMARY KEY(workspace_id, key)
        )
    """)

    # Populate active workspaces with brand new IDs
    new_group_ws = "new-group-ws-111"
    new_member_ws = "new-member-dm-222"

    cursor.execute("INSERT INTO workspaces VALUES (?, ?, 'group', 'Group Workspace', 1, 'now', 'now')",
                   (new_group_ws, -100123456789))
    cursor.execute("INSERT INTO workspaces VALUES (?, ?, 'private', 'Member Personal', 1, 'now', 'now')",
                   (new_member_ws, SYNTHETIC_MEMBER_ID))

    cursor.execute("INSERT INTO settings VALUES ('default_workspace_id', ?, 'now')", (new_group_ws,))
    cursor.execute("INSERT INTO workspace_settings VALUES (?, 'initial_balance', '0.0', 'now')", (new_group_ws,))
    cursor.execute("INSERT INTO workspace_settings VALUES (?, 'initial_balance', '0.0', 'now')", (new_member_ws,))

    # Populate rows carrying DEAD/OLD workspace IDs
    # 1. Chat match: old ws, group chat_id
    cursor.execute("""
        INSERT INTO transactions (id, workspace_id, telegram_chat_id, telegram_user_id, amount, uid)
        VALUES (1, 'dead-ws-old', '-100123456789', ?, 100.0, 'uid-1')
    """, (SYNTHETIC_MEMBER_ID,))

    # 2. User match: old ws, no chat_id, user_id is member (DM workspace chat_id == user_id)
    cursor.execute("""
        INSERT INTO transactions (id, workspace_id, telegram_chat_id, telegram_user_id, amount, uid)
        VALUES (2, 'dead-ws-old', '', ?, 200.0, 'uid-2')
    """, (SYNTHETIC_MEMBER_ID,))

    # 3. Owner fallback: old ws, no chat_id, user_id is owner -> moves to settings.default_workspace_id
    cursor.execute("""
        INSERT INTO transactions (id, workspace_id, telegram_chat_id, telegram_user_id, amount, uid)
        VALUES (3, 'dead-ws-old', '', ?, 300.0, 'uid-3')
    """, (SYNTHETIC_OWNER_ID,))

    # 4. Skip: old ws, no chat_id, unknown user
    cursor.execute("""
        INSERT INTO transactions (id, workspace_id, telegram_chat_id, telegram_user_id, amount, uid)
        VALUES (4, 'dead-ws-old', '', ?, 400.0, 'uid-4')
    """, (SYNTHETIC_UNKNOWN_USER_ID,))

    # Undo log rows
    cursor.execute("INSERT INTO undo_log (id, workspace_id, chat_id, user_id, action, uid, created_at) VALUES (10, 'dead-ws-old', -100123456789, ?, 'INSERT', 'uid-1', 'now')", (SYNTHETIC_MEMBER_ID,))
    cursor.execute("INSERT INTO undo_log (id, workspace_id, chat_id, user_id, action, uid, created_at) VALUES (20, 'dead-ws-old', NULL, ?, 'INSERT', 'uid-2', 'now')", (SYNTHETIC_MEMBER_ID,))
    cursor.execute("INSERT INTO undo_log (id, workspace_id, chat_id, user_id, action, uid, created_at) VALUES (30, 'dead-ws-old', NULL, ?, 'INSERT', 'uid-3', 'now')", (SYNTHETIC_OWNER_ID,))
    cursor.execute("INSERT INTO undo_log (id, workspace_id, chat_id, user_id, action, uid, created_at) VALUES (40, 'dead-ws-old', NULL, ?, 'INSERT', 'uid-4', 'now')", (SYNTHETIC_UNKNOWN_USER_ID,))

    conn.commit()
    conn.close()

    return {
        "db_file": db_file,
        "new_group_ws": new_group_ws,
        "new_member_ws": new_member_ws,
    }


def test_dry_run_identifies_all_fallback_reasons(fresh_db_setup):
    """Dry-run identifies chat_match, user_match, owner_default, and skip without modifying DB."""
    db_file = fresh_db_setup["db_file"]
    new_group_ws = fresh_db_setup["new_group_ws"]
    new_member_ws = fresh_db_setup["new_member_ws"]

    result = repair_provenance(conn_or_db_path=db_file, apply=False)
    assert result["success"] is True

    tx_summary = result["transactions"]
    assert tx_summary["scanned"] == 4
    assert tx_summary["retagged"] == 3  # 3 can be repaired, 1 skipped

    details_by_id = {d["id"]: d for d in tx_summary["details"]}

    # Tx 1: chat_match -> new_group_ws
    assert details_by_id[1]["reason"] == "chat_match"
    assert details_by_id[1]["to_workspace"] == new_group_ws
    assert details_by_id[1]["status"] == "would_retag"

    # Tx 2: user_match -> new_member_ws
    assert details_by_id[2]["reason"] == "user_match"
    assert details_by_id[2]["to_workspace"] == new_member_ws
    assert details_by_id[2]["status"] == "would_retag"

    # Tx 3: owner_default -> new_group_ws (default_workspace_id)
    assert details_by_id[3]["reason"] == "owner_default"
    assert details_by_id[3]["to_workspace"] == new_group_ws
    assert details_by_id[3]["status"] == "would_retag"

    # Tx 4: skipped -> no matching workspace
    assert details_by_id[4]["reason"] == "skipped:no_matching_workspace"
    assert details_by_id[4]["to_workspace"] is None
    assert details_by_id[4]["status"] == "skipped"

    # Verify DB was NOT modified in dry-run
    conn = sqlite3.connect(str(db_file))
    c = conn.cursor()
    c.execute("SELECT DISTINCT workspace_id FROM transactions")
    workspaces_in_db = {r[0] for r in c.fetchall()}
    conn.close()
    assert workspaces_in_db == {"dead-ws-old"}


def test_apply_commits_repairs_to_transactions_and_undo_log(fresh_db_setup):
    """Applying repairs successfully migrates transactions and undo_log to active workspaces."""
    db_file = fresh_db_setup["db_file"]
    new_group_ws = fresh_db_setup["new_group_ws"]
    new_member_ws = fresh_db_setup["new_member_ws"]

    result = repair_provenance(conn_or_db_path=db_file, apply=True)
    assert result["success"] is True

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Check transactions
    tx_rows = {r["id"]: r["workspace_id"] for r in c.execute("SELECT id, workspace_id FROM transactions").fetchall()}
    assert tx_rows[1] == new_group_ws   # chat_match
    assert tx_rows[2] == new_member_ws  # user_match
    assert tx_rows[3] == new_group_ws   # owner_default
    assert tx_rows[4] == "dead-ws-old"  # skipped, untouched

    # Check undo_log
    undo_rows = {r["id"]: r["workspace_id"] for r in c.execute("SELECT id, workspace_id FROM undo_log").fetchall()}
    assert undo_rows[10] == new_group_ws   # chat_match
    assert undo_rows[20] == new_member_ws  # user_match
    assert undo_rows[30] == new_group_ws   # owner_default
    assert undo_rows[40] == "dead-ws-old"  # skipped, untouched

    conn.close()


def test_ambiguous_chat_and_user_matches_are_safely_skipped(tmp_path, monkeypatch):
    """If multiple active workspaces claim the same chat_id, repair safely skips instead of guessing."""
    db_file = tmp_path / "test_ambiguous.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)

    conn = sqlite3.connect(str(db_file))
    c = conn.cursor()

    c.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, chat_id INTEGER, title TEXT, is_active INTEGER)")
    c.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, workspace_id TEXT, telegram_chat_id TEXT, telegram_user_id INTEGER)")
    c.execute("CREATE TABLE undo_log (id INTEGER PRIMARY KEY, workspace_id TEXT, chat_id INTEGER, user_id INTEGER)")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")

    # Two active workspaces with identical chat_id
    c.execute("INSERT INTO workspaces VALUES ('ws-dup-1', 99999, 'Dup 1', 1)")
    c.execute("INSERT INTO workspaces VALUES ('ws-dup-2', 99999, 'Dup 2', 1)")
    c.execute("INSERT INTO settings VALUES ('default_workspace_id', 'ws-dup-1')")

    c.execute("INSERT INTO transactions VALUES (1, 'dead-ws', '99999', NULL)")
    conn.commit()
    conn.close()

    result = repair_provenance(conn_or_db_path=db_file, apply=True)
    assert result["transactions"]["retagged"] == 0
    assert result["transactions"]["details"][0]["reason"] == "skipped:ambiguous_chat_match"
    assert result["transactions"]["details"][0]["status"] == "skipped"


# ==============================================================================
# WORK ITEM 3 TESTS — Honest /restore errors
# ==============================================================================

def test_import_backup_with_uid_conflict_skips_row_cleanly_without_exception(tmp_path, monkeypatch):
    """Importing a backup payload with a UID already taken by another workspace skips the row gracefully."""
    from database.db import setup_database
    from database.queries import get_or_create_workspace
    from services.backup_service import import_database_from_json
    import uuid

    db_file = tmp_path / "test_uid_conflict.sqlite3"
    monkeypatch.setattr("config.DB_PATH", db_file)
    monkeypatch.setattr("database.db.DB_PATH", db_file)
    monkeypatch.setattr("services.backup_service.DB_PATH", db_file)
    monkeypatch.setattr("config.TELEGRAM_USER_ID", SYNTHETIC_OWNER_ID)
    setup_database()

    ws_a = get_or_create_workspace(chat_id="-100111", chat_type="group", title="Workspace A", creator_user_id=SYNTHETIC_OWNER_ID)
    ws_b = get_or_create_workspace(chat_id="-100222", chat_type="group", title="Workspace B", creator_user_id=SYNTHETIC_OWNER_ID)
    ws_a_id = ws_a["id"] if isinstance(ws_a, dict) else ws_a.id
    ws_b_id = ws_b["id"] if isinstance(ws_b, dict) else ws_b.id

    conflict_uid = uuid.uuid4().hex

    # Insert an existing row in Workspace A
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO transactions (workspace_id, telegram_chat_id, telegram_user_id, amount, balance_before, balance_after, transaction_type, person_name, uid, occurred_at, created_at, updated_at)
        VALUES (?, '-100111', ?, 50.0, 0.0, -50.0, 'SENT', 'Merchant A', ?, '2026-10-10T00:00:00Z', '2026-10-10T00:00:00Z', '2026-10-10T00:00:00Z')
    """, (ws_a_id, SYNTHETIC_OWNER_ID, conflict_uid))
    conn.commit()
    conn.close()

    # Now attempt to import a workspace backup for Workspace B with the SAME UID
    from services.backup_service import compute_canonical_checksum
    payload = {
        "version": 2,
        "format_version": "workspace_v1",
        "workspace_id": ws_b_id,
        "revision": 10,
        "exported_at": "2026-10-10T12:00:00Z",
        "balance": 0.0,
        "transactions": [
            {
                "uid": conflict_uid,
                "amount": 100.0,
                "transaction_type": "SENT",
                "person_name": "Merchant B",
                "workspace_id": ws_b_id,
                "occurred_at": "2026-10-10T10:00:00Z",
                "created_at": "2026-10-10T10:00:00Z",
                "updated_at": "2026-10-10T10:00:00Z",
            }
        ],
        "settings": {},
        "custom_menu_items": [],
        "recurring_payments": [],
        "monthly_reviews": []
    }
    payload["checksum"] = compute_canonical_checksum(payload)

    # Import targeting Workspace B skips the colliding UID cleanly without throwing a constraint error
    res = import_database_from_json(data_dict=payload, target_workspace_id=ws_b_id)
    assert res["success"] is True
    assert res["skipped"] == 1
    assert res["inserted"] == 0

    # Ensure row in Workspace A was untouched
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT workspace_id, amount FROM transactions WHERE uid = ?", (conflict_uid,))
    row = cursor.fetchone()
    conn.close()
    assert row[0] == ws_a_id
    assert row[1] == 50.0


def test_restore_from_telegram_distinguishes_no_backup_vs_import_failure():
    """restore_from_telegram returns a result distinguishing between not found and failed import."""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock, patch
    from services.backup_service import restore_from_telegram, TelegramRestoreResult

    async def _test():
        # Case 1: No candidates in chats
        mock_bot_empty = MagicMock()
        mock_chat_empty = MagicMock()
        mock_chat_empty.pinned_message = None
        mock_bot_empty.get_chat = AsyncMock(return_value=mock_chat_empty)

        with patch("services.backup_service.restore_local_fallback_if_valid", return_value=False):
            res_empty = await restore_from_telegram(mock_bot_empty, chat_id="123456")
            assert isinstance(res_empty, TelegramRestoreResult)
            assert bool(res_empty) is False
            assert res_empty.success is False
            assert res_empty.found is False

        # Case 2: Candidate found, but download/import returns error
        mock_bot_fail = MagicMock()
        mock_chat_fail = MagicMock()
        mock_msg_fail = MagicMock()
        mock_doc = MagicMock()
        mock_doc.file_id = "file_fail_1"
        mock_doc.file_name = "backup.json"
        mock_msg_fail.document = mock_doc
        mock_msg_fail.caption = "#PAYMENT_TRACKER_BACKUP Rev 605"
        mock_chat_fail.pinned_message = mock_msg_fail
        mock_bot_fail.get_chat = AsyncMock(return_value=mock_chat_fail)

        mock_file = MagicMock()
        mock_file.download_to_drive = AsyncMock()
        mock_bot_fail.get_file = AsyncMock(return_value=mock_file)

        with patch("services.backup_service.import_database_from_json", return_value={"success": False, "error": "UNIQUE constraint failed: transactions.uid"}):
            res_fail = await restore_from_telegram(mock_bot_fail, chat_id="123456")
            assert isinstance(res_fail, TelegramRestoreResult)
            assert bool(res_fail) is False
            assert res_fail.success is False
            assert res_fail.found is True
            assert "UNIQUE constraint failed" in res_fail.error

    asyncio.run(_test())


def test_restore_command_displays_honest_error_on_cloud_restore_failure(tmp_path, monkeypatch):
    """restore_command displays the real error on import failure rather than misleading 'No backup file found'."""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock, patch
    from telegram import Update, User, Chat, Message
    from bot.commands import restore_command
    from services.backup_service import TelegramRestoreResult

    async def _test():
        non_existent_json = tmp_path / "absent_backup.json"
        monkeypatch.setattr("services.backup_service.BACKUP_JSON_PATH", non_existent_json)
        monkeypatch.setattr("config.TELEGRAM_USER_ID", SYNTHETIC_OWNER_ID)

        update = MagicMock(spec=Update)
        user = MagicMock(spec=User)
        user.id = SYNTHETIC_OWNER_ID
        chat = MagicMock(spec=Chat)
        chat.id = SYNTHETIC_OWNER_ID
        chat.type = "private"
        msg = MagicMock(spec=Message)
        msg.reply_to_message = None
        msg.reply_text = AsyncMock()
        update.effective_user = user
        update.effective_chat = chat
        update.effective_message = msg
        update.message = msg

        ctx = MagicMock()
        ctx.args = []
        ctx.bot = MagicMock()

        # 1. Cloud restore returns import failure (found=True, error="UNIQUE constraint failed: transactions.uid")
        fail_result = TelegramRestoreResult(success=False, found=True, error="UNIQUE constraint failed: transactions.uid")
        with patch("services.backup_service.restore_from_telegram", new_callable=AsyncMock) as mock_restore:
            mock_restore.return_value = fail_result
            await restore_command(update, ctx)

            # Verify the real error was shown and NOT "No backup file found"
            replies = [call[0][0] for call in msg.reply_text.call_args_list]
            assert any("Cloud Restore Failed" in r and "UNIQUE constraint failed" in r for r in replies)
            assert not any("No backup file found to restore from" in r for r in replies)

        # 2. Cloud restore genuinely not found (found=False)
        msg.reply_text.reset_mock()
        not_found_result = TelegramRestoreResult(success=False, found=False, error="No pinned backup found")
        with patch("services.backup_service.restore_from_telegram", new_callable=AsyncMock) as mock_restore:
            mock_restore.return_value = not_found_result
            await restore_command(update, ctx)

            replies = [call[0][0] for call in msg.reply_text.call_args_list]
            assert any("No backup file found to restore from" in r for r in replies)

    asyncio.run(_test())

