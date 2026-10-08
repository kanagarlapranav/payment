import sqlite3
import config

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("DB_PATH:", config.DB_PATH)
print("TELEGRAM_USER_ID:", config.TELEGRAM_USER_ID)
print("TELEGRAM_GROUP_ID:", config.TELEGRAM_GROUP_ID)

print("\n--- SETTINGS ---")
for r in c.execute("SELECT key, value FROM settings"):
    print(dict(r))

print("\n--- WORKSPACES ---")
for r in c.execute("SELECT id, chat_id, chat_type, title, is_active FROM workspaces"):
    print(dict(r))

print("\n--- WORKSPACE MEMBERS ---")
for r in c.execute("SELECT workspace_id, telegram_user_id, username, display_name, role, is_active FROM workspace_members"):
    print(dict(r))

print("\n--- ACCESS REQUESTS ---")
for r in c.execute("SELECT * FROM access_requests"):
    print(dict(r))

c.close()
conn.close()
