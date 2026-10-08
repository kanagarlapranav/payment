import sys; sys.path.insert(0, '.')
import sqlite3, config

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== TRANSACTIONS (last 15) ===")
for r in c.execute("SELECT id, transaction_type, amount, person_name, transaction_date, workspace_id FROM transactions ORDER BY id DESC LIMIT 15"):
    print(dict(r))

print()
print("=== TRANSACTION COUNT PER WORKSPACE ===")
for r in c.execute("SELECT workspace_id, count(*) as cnt FROM transactions GROUP BY workspace_id"):
    print(dict(r))

print()
print("=== WORKSPACE MEMBERS for Payment Group (d2b59f0c) ===")
ws_id = "d2b59f0c-e09a-40cf-9497-819cecfe4173"
for r in c.execute(
    "SELECT telegram_user_id, username, display_name, role, is_active FROM workspace_members WHERE workspace_id=?",
    (ws_id,)
):
    print(dict(r))

print()
print("=== ALL WORKSPACES ===")
for r in c.execute("SELECT id, chat_id, title, is_active FROM workspaces"):
    print(dict(r))

print()
print("=== SETTINGS (key ones) ===")
for r in c.execute("SELECT key, value FROM settings WHERE key IN ('default_workspace_id', 'current_balance', 'schema_version')"):
    print(dict(r))

conn.close()
