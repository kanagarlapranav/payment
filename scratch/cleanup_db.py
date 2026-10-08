"""
Clean up test/fake workspaces and members that are polluting the permissions panel.
Keeps only real data: Payment Group workspace and real users.
"""
import sys; sys.path.insert(0, '.')
import sqlite3, config
from database.db import get_db_connection, LEDGER_LOCK
from utils.dates import utc_now_iso

REAL_WORKSPACE_ID = 'd2b59f0c-e09a-40cf-9497-819cecfe4173'
REAL_CHAT_ID = -1004310685141
FAKE_WORKSPACE_IDS = [
    'e6873904-cd56-42cc-9ea9-6497f7ea462a',  # Finance Group (fake)
    '9016c5b9-6306-474d-b898-d80463d51c56',  # Duplicate Payment Group (fake)
    'b6e819de-ed5d-445b-8d87-f79f2cce07f4',  # User 90001 Personal (test)
    'a9fee686-71bb-41a2-a987-36209c8b984e',  # User 90002 Personal (test)
    'f0cffcb0-a4f8-4163-acb9-2f2fef950361',  # regular_member Personal (test)
]
FAKE_USER_IDS = [90001, 90002, 777666555]  # Test users only - never real

print("Cleaning test workspaces and fake users from database...")
now_utc = utc_now_iso()

with LEDGER_LOCK:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Delete fake workspace members (test users from all workspaces)
        for uid in FAKE_USER_IDS:
            cursor.execute("DELETE FROM workspace_members WHERE telegram_user_id = ?", (uid,))
            print(f"  Removed test user {uid} from all workspaces")
        
        # 2. Delete fake/test workspace member entries from fake workspaces  
        for ws_id in FAKE_WORKSPACE_IDS:
            cursor.execute("DELETE FROM workspace_members WHERE workspace_id = ?", (ws_id,))
            cursor.execute("DELETE FROM workspace_settings WHERE workspace_id = ?", (ws_id,))
            cursor.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
            print(f"  Removed fake workspace {ws_id}")
        
        # 3. Ensure Pranav (owner) is properly set in Payment Group
        cursor.execute("""
            INSERT OR REPLACE INTO workspace_members 
            (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
            VALUES (?, ?, ?, ?, 'owner', 1, ?, ?)
        """, (REAL_WORKSPACE_ID, 8379948573, 'pranav', 'Pranav (Owner)', now_utc, now_utc))
        print("  Ensured Pranav is owner in Payment Group")
        
        # 4. Ensure Nagendra is member (not owner) in Payment Group
        cursor.execute("""
            INSERT OR REPLACE INTO workspace_members
            (workspace_id, telegram_user_id, username, display_name, role, is_active, joined_at, updated_at)
            VALUES (?, ?, ?, ?, 'member', 1, ?, ?)
        """, (REAL_WORKSPACE_ID, 8343764796, 'nagendra', 'Nagendra', now_utc, now_utc))
        print("  Ensured Nagendra is member in Payment Group")

        conn.commit()

print()
print("=== Final workspace_members ===")
with get_db_connection() as conn:
    cursor = conn.cursor()
    for r in cursor.execute("SELECT workspace_id, telegram_user_id, username, display_name, role, is_active FROM workspace_members"):
        print(f"  {dict(r)}")

print()
print("=== Final workspaces ===")
with get_db_connection() as conn:
    cursor = conn.cursor()
    for r in cursor.execute("SELECT id, chat_id, title, is_active FROM workspaces"):
        print(f"  {dict(r)}")

print()
print("Done. Database cleaned.")
